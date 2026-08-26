"""Closed, validated read model for the company-research workbench."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.models import UnderwritingResearchVersion
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.workspace_draft import WorkspaceDraftService

ModuleState = Literal["not_started", "preparing", "needs_review", "ready", "blocked"]

_MODULE_ARTIFACTS = {
    "overview": "evidence_index",
    "business_map": "business_map",
    "operating_drivers": "driver_map",
    "evidence_and_gaps": "evidence_index",
    "industry_competition_regulation": "business_map",
    "financials_cash_flow_capital_allocation": "financial_bridge",
    "scenarios_valuation_implied_expectations": "valuation_set",
    "counterevidence_risks_next_checks": "research_gaps",
    "versions_changes_memo": "memo",
}


@dataclass(frozen=True, slots=True)
class WorkbenchArtifact:
    id: UUID
    kind: str
    version: int
    input_hash: str
    content_hash: str
    payload: dict
    source_refs: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class WorkbenchModule:
    key: str
    state: ModuleState
    artifact: WorkbenchArtifact | None


@dataclass(frozen=True, slots=True)
class WorkbenchCompany:
    id: UUID
    external_key: str
    canonical_name: str


@dataclass(frozen=True, slots=True)
class WorkbenchPreparation:
    id: UUID
    status: str
    current_step: str | None
    progress: int


@dataclass(frozen=True, slots=True)
class WorkbenchDraft:
    id: UUID
    lock_version: int
    base_revision_id: UUID | None


@dataclass(frozen=True, slots=True)
class CompanyResearchWorkspace:
    project_id: UUID
    company: WorkbenchCompany
    preparation: WorkbenchPreparation
    modules: tuple[WorkbenchModule, ...]
    source_count: int
    gap_count: int
    draft: WorkbenchDraft
    selected_revision: UUID | None
    change_summary: dict


@dataclass(frozen=True, slots=True)
class EvidenceReviewResult:
    evidence_artifact: WorkbenchArtifact


class CompanyResearchWorkbench:
    def __init__(self, session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._company = CompanyResearchRepository(session)
        self._product_repository = ProductRepository(session)
        self._drafts = WorkspaceDraftService(session, now=now)

    def _utcnow(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _artifact(
        row: CompanyResearchArtifactVersion, project_id: UUID
    ) -> WorkbenchArtifact:
        if row.project_id != project_id:
            raise ValidationError(
                "company research artifact belongs to another project"
            )
        # Repository access has already verified content hash; walk the full chain
        # in the caller to reject a plausible but disconnected current leaf.
        if not isinstance(row.payload, dict) or not isinstance(row.source_refs, list):
            raise ValidationError("company research artifact payload is invalid")
        expected_ref_keys = {"raw_hash", "source_locator", "source_role", "source_url"}
        if any(
            set(ref) != expected_ref_keys
            or not all(
                isinstance(ref[key], str) and ref[key].strip()
                for key in expected_ref_keys
            )
            or len(ref["raw_hash"]) != 64
            for ref in row.source_refs
        ):
            raise ValidationError("company research artifact source refs are invalid")
        if len({canonical_hash(ref) for ref in row.source_refs}) != len(
            row.source_refs
        ):
            raise ValidationError(
                "company research artifact source refs are duplicated"
            )
        if row.kind == "evidence_index":
            if set(row.payload) != {
                "fixture_content_hash",
                "cutoff",
                "company_external_key",
                "security_external_keys",
                "facts",
            }:
                raise ValidationError("evidence index payload is invalid")
            facts = row.payload.get("facts")
            if not isinstance(facts, list) or not facts:
                raise ValidationError("evidence index facts are invalid")
            keys = [fact.get("fact_key") for fact in facts if isinstance(fact, dict)]
            if (
                len(keys) != len(facts)
                or any(not isinstance(key, str) or not key for key in keys)
                or len(set(keys)) != len(keys)
            ):
                raise ValidationError("evidence index fact keys are invalid")
            required_fact_keys = {
                "fact_key",
                "company_external_key",
                "business_module",
                "metric_key",
                "value",
                "value_kind",
                "currency",
                "unit",
                "period_start",
                "period_end",
                "published_at",
                "available_at",
                "source_role",
                "source_url",
                "source_locator",
                "raw_hash",
            }
            if any(
                set(fact)
                not in (required_fact_keys, required_fact_keys | {"review_decision"})
                for fact in facts
            ):
                raise ValidationError("evidence index fact schema is invalid")
            if any(
                "review_decision" in fact
                and fact["review_decision"] not in {"confirmed", "rejected"}
                for fact in facts
            ):
                raise ValidationError("evidence index review decision is invalid")
        elif row.kind == "research_gaps":
            if set(row.payload) != {
                "fixture_content_hash",
                "company_external_key",
                "gaps",
            }:
                raise ValidationError("research gaps payload is invalid")
            gaps = row.payload["gaps"]
            if not isinstance(gaps, list) or any(
                not isinstance(gap, dict)
                or set(gap) != {"gap_key", "business_module", "reason"}
                for gap in gaps
            ):
                raise ValidationError("research gaps payload is invalid")
        elif row.kind == "business_map":
            if set(row.payload) != {
                "evidence_index_id",
                "evidence_content_hash",
                "modules",
            }:
                raise ValidationError("business map payload is invalid")
            modules = row.payload["modules"]
            if (
                not isinstance(modules, list)
                or not modules
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"key", "fact_keys"}
                    or not isinstance(item["key"], str)
                    or not item["key"]
                    or not isinstance(item["fact_keys"], list)
                    or not item["fact_keys"]
                    or not all(
                        isinstance(key, str) and key for key in item["fact_keys"]
                    )
                    for item in modules
                )
            ):
                raise ValidationError("business map payload is invalid")
        return WorkbenchArtifact(
            row.id,
            row.kind,
            row.version,
            row.input_hash,
            row.content_hash,
            row.payload,
            tuple(row.source_refs),
        )

    def _heads(self, project_id: UUID) -> dict[str, WorkbenchArtifact]:
        """Materialize all artifact families once; never query once per module."""
        rows = tuple(
            self._session.scalars(
                select(CompanyResearchArtifactVersion).where(
                    CompanyResearchArtifactVersion.project_id == project_id
                )
            )
        )
        by_id = {row.id: row for row in rows}
        successor_ids = {
            row.supersedes_id for row in rows if row.supersedes_id is not None
        }
        heads: dict[str, WorkbenchArtifact] = {}
        for row in rows:
            self._company._validate_artifact_row(row)
            if row.id in successor_ids:
                continue
            if row.kind in heads:
                raise ConflictError("multiple current artifact heads")
            # Validate the complete in-memory lineage, including project/kind,
            # monotonic version, and immutable parent content hash.
            current = row
            expected_version = row.version
            seen: set[UUID] = set()
            while True:
                if (
                    current.id in seen
                    or current.project_id != project_id
                    or current.kind != row.kind
                    or current.version != expected_version
                ):
                    raise ValidationError(
                        "company research artifact lineage is invalid"
                    )
                seen.add(current.id)
                if current.supersedes_id is None:
                    if current.version != 1 or current.parent_content_hash is not None:
                        raise ValidationError(
                            "company research artifact lineage is invalid"
                        )
                    break
                parent = by_id.get(current.supersedes_id)
                if parent is None or current.parent_content_hash != parent.content_hash:
                    raise ValidationError(
                        "company research artifact lineage is invalid"
                    )
                current, expected_version = parent, expected_version - 1
            heads[row.kind] = self._artifact(row, project_id)
        business_map = heads.get("business_map")
        evidence = heads.get("evidence_index")
        if business_map is not None:
            if (
                evidence is None
                or business_map.payload.get("evidence_index_id") != str(evidence.id)
                or business_map.payload.get("evidence_content_hash")
                != evidence.content_hash
            ):
                raise ValidationError("business map input evidence head is invalid")
        return heads

    def workspace(self, *, project_id: UUID) -> CompanyResearchWorkspace:
        # This deliberately avoids ``ResearchProjectService.status()``: that
        # projection loads every Security identity one-by-one, while a company
        # workbench needs neither Security identity nor a mutable identity
        # snapshot to render this operational state.
        record = self._product_repository.project(project_id)
        if record is None:
            raise ValidationError("company research project not found")
        project, _security_ids = record
        preparation = self._company.preparation_for_project(project_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        company_object = self._product_repository.object(project.primary_company_id)
        if company_object is None:
            raise ValidationError("company research project company is missing")
        draft = self._drafts.read(project_id)
        if draft is None:
            raise ValidationError("company research workspace draft is missing")
        if draft.base_revision_id is not None:
            revision = self._session.get(
                UnderwritingResearchVersion, draft.base_revision_id
            )
            if revision is None or revision.project_id != project_id:
                raise ValidationError("workspace selected revision is invalid")
            # Existing product revisions intentionally contain no company
            # workbench model refs.  Serving the current artifact heads in
            # their place would fabricate a historical workspace, so fail
            # closed until a revision carries frozen company-research refs.
            manifest = self._product_repository.manifest(revision.manifest_id)
            refs = (
                manifest.manifest.get("model_refs")
                if manifest is not None and isinstance(manifest.manifest, dict)
                else None
            )
            if not isinstance(refs, list) or not refs:
                raise ValidationError(
                    "workspace selected revision has no frozen company research manifest"
                )
            raise ValidationError(
                "frozen company research workspace replay is not implemented"
            )
        heads = self._heads(project_id)
        modules = []
        for key, kind in _MODULE_ARTIFACTS.items():
            artifact = heads.get(kind)
            if (
                artifact is not None
                and kind == "evidence_index"
                and preparation.status == "awaiting_evidence_review"
            ):
                state = "needs_review"
            elif artifact is not None:
                state: ModuleState = "ready"
            elif preparation.status in {"blocked", "recoverable_failure"}:
                state = "blocked"
            elif preparation.status in {"preparing_sources", "building_model"}:
                state = "preparing"
            else:
                state = "not_started"
            modules.append(WorkbenchModule(key, state, artifact))
        gaps = heads.get("research_gaps")
        gap_values = gaps.payload.get("gaps", []) if gaps else []
        if not isinstance(gap_values, list):
            raise ValidationError("research gaps payload is invalid")
        evidence_facts = (
            heads.get("evidence_index").payload.get("facts", [])
            if heads.get("evidence_index")
            else []
        )
        reviewed_fact_count = sum(
            1
            for fact in evidence_facts
            if isinstance(fact, dict)
            and fact.get("review_decision") in {"confirmed", "rejected"}
        )
        return CompanyResearchWorkspace(
            project_id,
            WorkbenchCompany(
                project.primary_company_id,
                company_object.external_key,
                company_object.canonical_name,
            ),
            WorkbenchPreparation(
                preparation.id,
                preparation.status,
                preparation.current_step,
                preparation.progress,
            ),
            tuple(modules),
            len(
                {
                    canonical_hash(ref)
                    for item in heads.values()
                    for ref in item.source_refs
                }
            ),
            len(gap_values),
            WorkbenchDraft(draft.id, draft.lock_version, draft.base_revision_id),
            draft.base_revision_id,
            {
                "artifact_versions": {
                    kind: artifact.version for kind, artifact in sorted(heads.items())
                },
                "reviewed_fact_count": reviewed_fact_count,
            },
        )

    def review_evidence(
        self,
        *,
        project_id: UUID,
        evidence_artifact_id: UUID,
        fact_key: str,
        decision: Literal["confirmed", "rejected"],
        expected_head_id: UUID,
    ) -> EvidenceReviewResult:
        if (
            not isinstance(fact_key, str)
            or not fact_key
            or fact_key != fact_key.strip()
            or len(fact_key) > 160
        ):
            raise ValidationError("fact_key is invalid")
        if decision not in {"confirmed", "rejected"}:
            raise ValidationError("evidence review decision is invalid")
        self._product_repository.lock_project(project_id)
        preparation = self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.project_id == project_id)
            .with_for_update()
        )
        if preparation is None:
            raise ValidationError("company research preparation not found")
        if preparation.job_id is None:
            raise ValidationError("company research preparation job is missing")
        job = self._session.scalar(
            select(Job).where(Job.id == preparation.job_id).with_for_update()
        )
        if not self._company.is_exact_prepare_job_owner(job, preparation.id):
            raise ValidationError(
                "company research preparation job ownership is invalid"
            )
        if job.status != "waiting_for_review" or job.step != "research_gaps":
            raise ValidationError("company research evidence review job is not current")
        candidate = self._company.artifact(evidence_artifact_id)
        if (
            candidate is None
            or candidate.project_id != project_id
            or candidate.kind != "evidence_index"
        ):
            raise ValidationError("evidence artifact is not a project candidate")
        head = self._company.current_artifact(project_id, "evidence_index", lock=True)
        if head is None:
            raise ValidationError("evidence artifact is missing")
        if head.id != expected_head_id:
            # Exact retries identify the old candidate and decision, never a
            # merely similar current head.
            if (
                candidate.id == evidence_artifact_id
                and head.supersedes_id == candidate.id
            ):
                reviewed = next(
                    (
                        x
                        for x in head.payload.get("facts", [])
                        if isinstance(x, dict) and x.get("fact_key") == fact_key
                    ),
                    None,
                )
                if reviewed is not None and reviewed.get("review_decision") == decision:
                    return EvidenceReviewResult(self._artifact(head, project_id))
            raise ConflictError("expected evidence artifact is not the current head")
        if candidate.id != expected_head_id:
            raise ConflictError("evidence artifact and expected head differ")
        if preparation.status != "awaiting_evidence_review":
            raise ValidationError("company research evidence is not awaiting review")
        payload = dict(candidate.payload)
        facts = payload.get("facts")
        if not isinstance(facts, list):
            raise ValidationError("evidence index facts are invalid")
        updated: list[dict] = []
        found = False
        for fact in facts:
            if not isinstance(fact, dict):
                raise ValidationError("evidence index fact is invalid")
            copy = dict(fact)
            if copy.get("fact_key") == fact_key:
                found = True
                if "review_decision" in copy:
                    raise ValidationError("evidence fact is already reviewed")
                copy["review_decision"] = decision
            updated.append(copy)
        if not found:
            raise ValidationError("evidence fact is unknown")
        payload["facts"] = updated
        now = self._utcnow()
        successor = self._company.append_artifact(
            project_id=project_id,
            kind="evidence_index",
            input_hash=canonical_hash(
                {
                    "parent": candidate.content_hash,
                    "fact_key": fact_key,
                    "decision": decision,
                }
            ),
            payload=payload,
            source_refs=candidate.source_refs,
            expected_parent_id=candidate.id,
            created_at=now,
        )
        self._company.append_event(
            preparation_id=preparation.id,
            event_type="evidence_reviewed",
            payload={
                "evidence_artifact_id": str(successor.id),
                "fact_key": fact_key,
                "decision": decision,
            },
            created_at=now,
        )
        all_reviewed = all(
            item.get("review_decision") in {"confirmed", "rejected"} for item in updated
        )
        if all_reviewed:
            job.status, job.step, job.progress, job.claim_token = (
                "queued",
                "business_map",
                25,
                None,
            )
            preparation.status, preparation.current_step, preparation.progress = (
                "building_model",
                "business_map",
                25,
            )
        else:
            job.status, job.step, job.progress, job.claim_token = (
                "waiting_for_review",
                "research_gaps",
                25,
                None,
            )
            preparation.status, preparation.current_step, preparation.progress = (
                "awaiting_evidence_review",
                "research_gaps",
                25,
            )
        preparation.updated_at = now
        self._session.flush([job, preparation])
        return EvidenceReviewResult(self._artifact(successor, project_id))
