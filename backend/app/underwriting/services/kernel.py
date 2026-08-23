"""Validated write operations for the underwriting research kernel."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import re
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.answerability import (
    AnswerabilityInput,
    enforce_action_boundary,
    evaluate_answerability,
)
from app.underwriting.domain.types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
    HistoricalBasisInput,
    InvestmentMandateInput,
    LedgerEntryInput,
    ResearchObjectKind,
)
from app.underwriting.persistence.models import UnderwritingLedgerEntry
from app.underwriting.persistence.repository import StaleParentError, UnderwritingRepository


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_RESEARCH_VERSION_PARENT_SET_SCHEMA = "underwriting.research-version-parent-set.v3"
_RELATION_KINDS = {
    "industry_exposes_company": (
        ResearchObjectKind.INDUSTRY.value,
        ResearchObjectKind.COMPANY.value,
    ),
    "company_has_security": (
        ResearchObjectKind.COMPANY.value,
        ResearchObjectKind.SECURITY.value,
    ),
}


@dataclass(frozen=True, slots=True)
class KernelSnapshot:
    object_id: uuid.UUID
    basis_id: uuid.UUID
    cutoff: datetime
    entries: tuple[UnderwritingLedgerEntry, ...]
    snapshot_hash: str


def canonical_hash(value: object) -> str:
    """Return the stable SHA-256 digest for a JSON-compatible value."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def frozen_research_version_content_hash(
    object_id: uuid.UUID,
    basis_id: uuid.UUID,
    version_kind: str,
    parent_ids: list[str],
    *,
    cutoff: datetime,
    price_as_of: datetime | None,
    source_manifest_hash: str,
    parent_refs: tuple[Mapping[str, object], ...],
) -> tuple[str, str, list[str]]:
    """Seal a generic research version from frozen parents and historical basis.

    A research-version row is a historical assertion.  Its integrity cannot
    depend on the currently effective ledger, because unrelated evidence may
    be appended after publication.  Payload authenticity is verified by the
    reader when each frozen parent is resolved; this seal binds that exact
    parent-id set and authenticated parent descriptors to its object, basis, version family, and the complete
    historically knowable basis.  ``basis_id`` alone is not an integrity
    boundary: an unsafe database update could otherwise alter its cutoff,
    price timestamp, or source-manifest identity without invalidating history.
    """
    if not isinstance(version_kind, str) or not (normalized_kind := version_kind.strip()):
        raise ValidationError("version_kind must not be empty")
    if not isinstance(parent_ids, list) or not all(isinstance(value, str) for value in parent_ids):
        raise ValidationError("research revision parents are malformed")
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValidationError("research revision basis cutoff must be timezone-aware")
    if price_as_of is not None and (price_as_of.tzinfo is None or price_as_of.utcoffset() is None):
        raise ValidationError("research revision basis price_as_of must be timezone-aware")
    if not isinstance(source_manifest_hash, str) or _SHA256_HEX.fullmatch(source_manifest_hash) is None:
        raise ValidationError("research revision basis source_manifest_hash must be a sha256 hex digest")
    normalized_parent_ids = sorted(set(parent_ids))
    required_parent_ref_keys = frozenset({"reference", "artifact_type", "identity", "content_hash"})
    normalized_parent_refs: list[dict[str, str]] = []
    for parent_ref in parent_refs:
        if not isinstance(parent_ref, Mapping) or set(parent_ref) != required_parent_ref_keys:
            raise ValidationError("research revision parent descriptors are malformed")
        normalized_ref: dict[str, str] = {}
        for key in required_parent_ref_keys:
            value = parent_ref[key]
            if not isinstance(value, str) or not value:
                raise ValidationError("research revision parent descriptors are malformed")
            normalized_ref[key] = value
        normalized_parent_refs.append(normalized_ref)
    normalized_parent_refs.sort(
        key=lambda ref: (ref["artifact_type"], ref["identity"], ref["reference"], ref["content_hash"])
    )
    if len(normalized_parent_refs) != len(normalized_parent_ids):
        raise ValidationError("research revision parent descriptors do not match parent set")
    if {ref["reference"] for ref in normalized_parent_refs} != set(normalized_parent_ids):
        raise ValidationError("research revision parent descriptors do not match parent set")
    if len({(ref["reference"], ref["artifact_type"]) for ref in normalized_parent_refs}) != len(normalized_parent_refs):
        raise ValidationError("research revision parent descriptors are duplicated")
    return (
        canonical_hash({
            "schema_version": _RESEARCH_VERSION_PARENT_SET_SCHEMA,
            "object_id": str(object_id),
            "basis_id": str(basis_id),
            "basis": {
                "cutoff": cutoff.astimezone(UTC).isoformat(),
                "price_as_of": price_as_of.astimezone(UTC).isoformat() if price_as_of is not None else None,
                "source_manifest_hash": source_manifest_hash,
            },
            "version_kind": normalized_kind,
            "parent_ids": normalized_parent_ids,
            "parent_refs": normalized_parent_refs,
        }),
        normalized_kind,
        normalized_parent_ids,
    )


class UnderwritingKernelService:
    """Validate and append immutable underwriting kernel records."""

    def __init__(self, session: Session, now: Callable[[], datetime]) -> None:
        self._session = session
        self._repository = UnderwritingRepository(session)
        self._now = now

    @staticmethod
    def _require_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValidationError(f"{field} must not be empty")
        return normalized

    @staticmethod
    def _require_timezone(value: datetime, field: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError(f"{field} must be timezone-aware")

    @staticmethod
    def _stored_datetime(value: datetime) -> datetime:
        """Restore SQLite's UTC wall-clock representation after a reload.

        New underwriting rows did not exist before migration 0060 and are
        normalized to UTC by this service before they are written.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _normalize_datetime(cls, value: datetime, field: str) -> datetime:
        cls._require_timezone(value, field)
        return value.astimezone(UTC)

    def _write(self, conflict_message: str, operation: Callable[[], object]):
        try:
            return operation()
        except IntegrityError as exc:
            self._session.rollback()
            raise ConflictError(conflict_message) from exc

    def add_object(
        self,
        kind: ResearchObjectKind,
        external_key: str,
        canonical_name: str,
    ):
        return self._write(
            "research object already exists",
            lambda: self._repository.add_object(
                kind=kind.value,
                external_key=self._require_text(external_key, "external_key"),
                canonical_name=self._require_text(canonical_name, "canonical_name"),
                created_at=self._now(),
            ),
        )

    def link_objects(
        self,
        parent_id: uuid.UUID,
        child_id: uuid.UUID,
        relation_type: str,
    ):
        try:
            expected_parent_kind, expected_child_kind = _RELATION_KINDS[relation_type]
        except KeyError as exc:
            raise ValidationError("relation_type is invalid") from exc

        parent = self._repository.object(parent_id)
        child = self._repository.object(child_id)
        if parent is None or child is None:
            raise ValidationError("research object not found")
        if parent.kind != expected_parent_kind or child.kind != expected_child_kind:
            raise ValidationError(
                f"{relation_type} requires {expected_parent_kind} parent and "
                f"{expected_child_kind} child"
            )
        return self._write(
            "research object relation already exists",
            lambda: self._repository.add_relation(
                parent_id=parent_id,
                child_id=child_id,
                relation_type=relation_type,
                created_at=self._now(),
            ),
        )

    def add_basis(self, value: HistoricalBasisInput):
        cutoff = self._normalize_datetime(value.cutoff, "cutoff")
        price_as_of = (
            self._normalize_datetime(value.price_as_of, "price_as_of")
            if value.price_as_of is not None
            else None
        )
        if price_as_of is not None:
            if price_as_of > cutoff:
                raise ValidationError("price_as_of must not be after cutoff")
        if not isinstance(value.source_manifest_hash, str) or not _SHA256_HEX.fullmatch(
            value.source_manifest_hash
        ):
            raise ValidationError("source_manifest_hash must be a sha256 hex digest")
        return self._write(
            "historical basis write conflicts",
            lambda: self._repository.add_basis(
                HistoricalBasisInput(cutoff, price_as_of, value.source_manifest_hash),
                created_at=self._now(),
            ),
        )

    def append_ledger_entry(
        self,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        value: LedgerEntryInput,
        expected_parent_id: uuid.UUID | None,
    ):
        research_object = self._repository.object(object_id)
        if research_object is None:
            raise ValidationError("research object not found")
        basis = self._repository.basis(basis_id)
        if basis is None:
            raise ValidationError("historical basis not found")

        effective_at = self._normalize_datetime(value.effective_at, "effective_at")
        available_at = self._normalize_datetime(value.available_at, "available_at")
        if available_at > self._stored_datetime(basis.cutoff):
            raise ValidationError("available_at must not be after basis cutoff")

        family_key = self._require_text(value.family_key, "family_key")
        entry_type = self._require_text(value.entry_type, "entry_type")
        source_boundary = self._require_text(value.source_boundary, "source_boundary")
        ledger_kind = value.ledger_kind.value
        payload = copy.deepcopy(value.payload)
        content_hash = canonical_hash(
            {
                "ledger_kind": ledger_kind,
                "family_key": family_key,
                "entry_type": entry_type,
                "payload": payload,
                "effective_at": effective_at,
                "available_at": available_at,
                "source_boundary": source_boundary,
            }
        )
        try:
            return self._write(
                "ledger entry write conflicts",
                lambda: self._repository.append_ledger_entry(
                    object_id=object_id,
                    basis_id=basis_id,
                    ledger_kind=ledger_kind,
                    family_key=family_key,
                    entry_type=entry_type,
                    payload=payload,
                    effective_at=effective_at,
                    available_at=available_at,
                    source_boundary=source_boundary,
                    content_hash=content_hash,
                    expected_parent_id=expected_parent_id,
                    created_at=self._now(),
                ),
            )
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc

    def record_answerability(
        self,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        hard_blockers: tuple[BlockerCode, ...],
        research_debt_keys: tuple[str, ...],
        resolvable_within_mandate: bool,
        requested_action: EligibleAction,
        resolution_requirements: tuple[str, ...],
        expected_parent_id: uuid.UUID | None,
        *,
        created_at: datetime | None = None,
    ):
        if self._repository.object(object_id) is None:
            raise ValidationError("research object not found")
        basis = self._repository.basis(basis_id)
        if basis is None:
            raise ValidationError("historical basis not found")
        recorded_at = (
            self._normalize_datetime(created_at, "answerability created_at")
            if created_at is not None
            else self._now()
        )
        if created_at is not None and recorded_at > self._stored_datetime(basis.cutoff):
            raise ValidationError("answerability created_at must not exceed historical basis cutoff")

        result = evaluate_answerability(
            AnswerabilityInput(
                hard_blockers=hard_blockers,
                research_debt_keys=research_debt_keys,
                resolvable_within_mandate=resolvable_within_mandate,
            )
        )
        if (
            result.state is AnswerabilityState.NOT_ANSWERABLE
            and not resolution_requirements
        ):
            raise ValidationError(
                "resolution_requirements must not be empty for not_answerable"
            )
        allowed_action = enforce_action_boundary(result, requested_action)

        try:
            return self._write(
                "answerability evaluation write conflicts",
                lambda: self._repository.append_answerability_evaluation(
                    object_id=object_id,
                    basis_id=basis_id,
                    state=result.state.value,
                    blockers=[blocker.value for blocker in result.blockers],
                    research_debt_keys=list(research_debt_keys),
                    resolvable_within_mandate=result.resolvable_within_mandate,
                    allowed_action=allowed_action.value,
                    resolution_requirements=list(resolution_requirements),
                    expected_parent_id=expected_parent_id,
                    created_at=recorded_at,
                ),
            )
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc

    def snapshot(self, object_id: uuid.UUID, basis_id: uuid.UUID) -> KernelSnapshot:
        if self._repository.object(object_id) is None:
            raise ValidationError("research object not found")
        basis = self._repository.basis(basis_id)
        if basis is None:
            raise ValidationError("historical basis not found")

        cutoff = self._stored_datetime(basis.cutoff)
        entries = tuple(self._repository.effective_entries_at(object_id, cutoff))
        snapshot_hash = canonical_hash(
            {
                "object_id": object_id,
                "basis_cutoff": cutoff,
                "source_manifest_hash": basis.source_manifest_hash,
                "entries": [
                    {"id": entry.id, "content_hash": entry.content_hash}
                    for entry in entries
                ],
            }
        )
        return KernelSnapshot(object_id, basis_id, cutoff, entries, snapshot_hash)

    def preview_research_version_hash(
        self,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        version_kind: str,
        parent_ids: list[str],
    ) -> str:
        if self._repository.object(object_id) is None:
            raise ValidationError("research object not found")
        basis = self._repository.basis(basis_id)
        if basis is None:
            raise ValidationError("historical basis not found")
        from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

        parent_refs = ResearchRevisionDiffService(self._session).frozen_parent_descriptors(
            object_id, basis_id, version_kind, parent_ids,
        )
        content_hash, _, _ = frozen_research_version_content_hash(
            object_id, basis_id, version_kind, parent_ids,
            cutoff=self._stored_datetime(basis.cutoff),
            price_as_of=self._stored_datetime(basis.price_as_of) if basis.price_as_of is not None else None,
            source_manifest_hash=basis.source_manifest_hash,
            parent_refs=parent_refs,
        )
        return content_hash

    def publish_research_version(
        self,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        version_kind: str,
        parent_ids: list[str],
        expected_parent_id: uuid.UUID | None,
    ):
        if self._repository.object(object_id) is None:
            raise ValidationError("research object not found")
        basis = self._repository.basis(basis_id)
        if basis is None:
            raise ValidationError("historical basis not found")
        from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

        parent_refs = ResearchRevisionDiffService(self._session).frozen_parent_descriptors(
            object_id, basis_id, version_kind, parent_ids,
        )
        content_hash, normalized_kind, normalized_parent_ids = (
            frozen_research_version_content_hash(
                object_id, basis_id, version_kind, parent_ids,
                cutoff=self._stored_datetime(basis.cutoff),
                price_as_of=self._stored_datetime(basis.price_as_of) if basis.price_as_of is not None else None,
                source_manifest_hash=basis.source_manifest_hash,
                parent_refs=parent_refs,
            )
        )
        try:
            return self._write(
                "research version write conflicts",
                lambda: self._repository.append_research_version(
                    object_id=object_id,
                    basis_id=basis_id,
                    version_kind=normalized_kind,
                    content_hash=content_hash,
                    parent_ids=normalized_parent_ids,
                    expected_parent_id=expected_parent_id,
                    created_at=self._now(),
                ),
            )
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc

    def _validate_mandate(self, value: InvestmentMandateInput) -> dict[str, object]:
        mandate_key = self._require_text(value.mandate_key, "mandate_key")
        if value.horizon_years not in (3, 4, 5):
            raise ValidationError("horizon_years must be one of 3, 4, 5")
        base_currency = self._require_text(value.base_currency, "base_currency")
        if not re.fullmatch(r"[A-Z]{3}", base_currency):
            raise ValidationError("base_currency must be a three-letter uppercase code")
        if not Decimal("0") <= value.required_return < Decimal("1"):
            raise ValidationError("required_return must be in [0, 1)")
        if not Decimal("0") <= value.permanent_loss_limit <= Decimal("1"):
            raise ValidationError("permanent_loss_limit must be in [0, 1]")
        if not value.comparison_set:
            raise ValidationError("comparison_set must not be empty")
        return {
            "mandate_key": mandate_key,
            "horizon_years": value.horizon_years,
            "base_currency": base_currency,
            "required_return": value.required_return,
            "permanent_loss_limit": value.permanent_loss_limit,
            "comparison_set": [
                self._require_text(item, "comparison_set")
                for item in value.comparison_set
            ],
        }

    def append_mandate(
        self,
        value: InvestmentMandateInput,
        expected_parent_id: uuid.UUID | None,
    ):
        normalized = self._validate_mandate(value)
        try:
            return self._write(
                "mandate write conflicts",
                lambda: self._repository.append_mandate_version(
                    **normalized,
                    expected_parent_id=expected_parent_id,
                    created_at=self._now(),
                ),
            )
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc
