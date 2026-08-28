"""Closed, validated read model for the company-research workbench."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    CompanyResearchMemoArtifact,
    JudgmentContextArtifact,
    ResearchGap,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.domain.company_research_provenance import canonical_source_refs
from app.underwriting.persistence.models import UnderwritingResearchVersion
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
    reconcile_company_research_evidence_audit,
    validate_company_research_derived_gap_semantics,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService
from app.underwriting.services.company_research_model_builder import (
    validate_company_research_evidence_payload_for_read,
)
from app.underwriting.services.company_research_sources import (
    CompanyResearchProviderInput,
    CompanyResearchSourceCompiler,
)

ModuleState = Literal["not_started", "preparing", "needs_review", "ready", "blocked"]
_MAX_ARTIFACT_HISTORY = 2048

_MODULE_ARTIFACTS = {
    "overview": ("judgment_context",),
    "business_map": ("business_map",),
    "operating_drivers": ("driver_map",),
    "evidence_and_gaps": ("evidence_index", "research_gaps"),
    "industry_competition_regulation": ("business_map",),
    "financials_cash_flow_capital_allocation": ("financial_bridge",),
    "scenarios_valuation_implied_expectations": ("scenario_set", "valuation_set"),
    "counterevidence_risks_next_checks": ("research_gaps", "judgment_context"),
    "versions_changes_memo": ("memo",),
}
_ARTIFACT_ORDER = (
    "evidence_index",
    "research_gaps",
    "business_map",
    "driver_map",
    "financial_bridge",
    "scenario_set",
    "valuation_set",
    "judgment_context",
    "memo",
)


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
    artifacts: tuple[WorkbenchArtifact, ...]
    valuation_state: Literal["not_applicable", "pending", "ready", "blocked"]

    @property
    def artifact(self) -> WorkbenchArtifact | None:
        return self.artifacts[0] if self.artifacts else None


@dataclass(frozen=True, slots=True)
class WorkbenchCompany:
    id: UUID
    external_key: str
    canonical_name: str


@dataclass(frozen=True, slots=True)
class WorkbenchPreparation:
    id: UUID
    strategy_version: str
    status: str
    current_step: str | None
    progress: int
    error: "WorkbenchPreparationError | None"


@dataclass(frozen=True, slots=True)
class WorkbenchPreparationError:
    code: str
    failed_step: str
    retryable: bool
    next_attempt_at: datetime | None


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
    artifacts: tuple[WorkbenchArtifact, ...]
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
        closed_model_artifact = "_lineage" in row.payload
        if closed_model_artifact:
            CompanyResearchArtifactCodec.validate_payload(
                row.kind,
                {key: value for key, value in row.payload.items() if key != "_lineage"},
            )
        if row.kind == "evidence_index":
            validate_company_research_evidence_payload_for_read(row.payload)
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
        elif row.kind == "research_gaps" and not closed_model_artifact:
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
        elif row.kind == "business_map" and not closed_model_artifact:
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

    @staticmethod
    def _validate_evidence_review_chain(
        chain: tuple[CompanyResearchArtifactVersion, ...],
        *,
        preparation: CompanyResearchPreparation,
        company_external_key: str,
    ) -> None:
        if not chain:
            raise ValidationError("company research evidence lineage is invalid")
        for row in chain:
            validate_company_research_evidence_payload_for_read(row.payload)
        expected = CompanyResearchSourceCompiler().compile_evidence_index(
            CompanyResearchProviderInput(
                preparation_id=preparation.id,
                project_id=preparation.project_id,
                company_external_key=company_external_key,
                request_hash=preparation.request_hash,
                strategy_version=preparation.strategy_version,
            )
        )
        root = chain[0]
        if (
            root.input_hash != expected.input_hash
            or root.payload != expected.evidence_index_payload
            or root.source_refs != list(expected.source_refs)
        ):
            raise ValidationError("company research evidence lineage is invalid")
        root_facts = chain[0].payload.get("facts")
        if not isinstance(root_facts, list) or any(
            isinstance(fact, Mapping) and "review_decision" in fact
            for fact in root_facts
        ):
            raise ValidationError("company research evidence lineage is invalid")
        for parent, successor in zip(chain, chain[1:], strict=False):
            if successor.source_refs != parent.source_refs:
                raise ValidationError("company research evidence lineage is invalid")
            before_facts = parent.payload.get("facts")
            after_facts = successor.payload.get("facts")
            if (
                not isinstance(before_facts, list)
                or not isinstance(after_facts, list)
                or len(before_facts) != len(after_facts)
            ):
                raise ValidationError("company research evidence lineage is invalid")
            changed: list[tuple[int, str, str, dict[str, object]]] = []
            for index, (before, after) in enumerate(
                zip(before_facts, after_facts, strict=True)
            ):
                if before == after:
                    continue
                if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                    raise ValidationError(
                        "company research evidence lineage is invalid"
                    )
                decision = after.get("review_decision")
                fact_key = after.get("fact_key")
                expected = dict(before)
                if (
                    "review_decision" in before
                    or not isinstance(decision, str)
                    or decision not in {"confirmed", "rejected"}
                    or not isinstance(fact_key, str)
                    or not fact_key
                ):
                    raise ValidationError(
                        "company research evidence lineage is invalid"
                    )
                expected["review_decision"] = decision
                if dict(after) != expected:
                    raise ValidationError(
                        "company research evidence lineage is invalid"
                    )
                changed.append((index, fact_key, decision, expected))
            if len(changed) != 1:
                raise ValidationError("company research evidence lineage is invalid")
            index, fact_key, decision, expected_fact = changed[0]
            expected_payload = dict(parent.payload)
            expected_facts = [dict(fact) for fact in before_facts]
            expected_facts[index] = expected_fact
            expected_payload["facts"] = expected_facts
            if successor.payload != expected_payload or successor.input_hash != canonical_hash(
                {
                    "parent": parent.content_hash,
                    "fact_key": fact_key,
                    "decision": decision,
                }
            ):
                raise ValidationError("company research evidence lineage is invalid")

    @staticmethod
    def _lineage_reference(row: CompanyResearchArtifactVersion) -> dict[str, str]:
        return {
            "artifact_id": str(row.id),
            "artifact_kind": row.kind,
            "content_hash": row.content_hash,
        }

    @staticmethod
    def _validate_closed_lineage_shape(
        row: CompanyResearchArtifactVersion,
    ) -> tuple[list[dict[str, str]], list[str], list[dict[str, object]]]:
        lineage = row.payload.get("_lineage")
        if not isinstance(lineage, dict) or set(lineage) != {
            "artifact_refs",
            "market_snapshot_ids",
            "market_snapshot_bindings",
        }:
            raise ValidationError("company research cross-artifact lineage is invalid")
        refs = lineage["artifact_refs"]
        snapshot_ids = lineage["market_snapshot_ids"]
        bindings = lineage["market_snapshot_bindings"]
        if (
            not isinstance(refs, list)
            or any(
                not isinstance(ref, dict)
                or set(ref) != {"artifact_id", "artifact_kind", "content_hash"}
                or not isinstance(ref["artifact_id"], str)
                or not isinstance(ref["artifact_kind"], str)
                or not isinstance(ref["content_hash"], str)
                for ref in refs
            )
            or len({canonical_hash(ref) for ref in refs}) != len(refs)
            or not isinstance(snapshot_ids, list)
            or any(not isinstance(value, str) for value in snapshot_ids)
            or len(set(snapshot_ids)) != len(snapshot_ids)
            or sorted(snapshot_ids) != snapshot_ids
            or not isinstance(bindings, list)
            or len(bindings) != len(snapshot_ids)
        ):
            raise ValidationError("company research cross-artifact lineage is invalid")
        try:
            if any(str(UUID(value)) != value for value in snapshot_ids):
                raise ValueError
        except ValueError as exc:
            raise ValidationError(
                "company research cross-artifact lineage is invalid"
            ) from exc
        parsed_bindings = tuple(
            CompanyResearchRepository.market_binding_from_payload(value)
            for value in bindings
        )
        if [str(value.snapshot_id) for value in parsed_bindings] != snapshot_ids:
            raise ValidationError("company research cross-artifact lineage is invalid")
        return refs, snapshot_ids, bindings

    def _validate_cross_artifact_lineage(
        self,
        project_id: UUID,
        heads: dict[str, CompanyResearchArtifactVersion],
        history_by_id: Mapping[UUID, CompanyResearchArtifactVersion],
        *,
        expected_draft_id: UUID,
        expected_draft_lock_version: int,
    ) -> CompanyResearchArtifactVersion | None:
        """Reject a partial, substituted, or cross-project model bundle."""
        model_kinds = {
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "memo",
        }
        if not any(kind in heads for kind in model_kinds - {"business_map"}):
            return None
        required = {
            "evidence_index",
            "research_gaps",
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "judgment_context",
            "memo",
        }
        if not required <= heads.keys():
            raise ValidationError("company research cross-artifact lineage is invalid")
        preparation = self._company.preparation_for_project(project_id)
        if preparation is None:
            raise ValidationError("company research cross-artifact lineage is invalid")

        def expected(*kinds: str) -> list[dict[str, str]]:
            return [self._lineage_reference(heads[kind]) for kind in kinds]

        memo_has_embedded_gaps = "research_gaps" in heads["memo"].payload
        expectations: dict[str, list[dict[str, str]]] = {
            "business_map": expected("evidence_index"),
            "driver_map": expected("business_map"),
            "financial_bridge": expected("driver_map"),
            "scenario_set": expected("driver_map"),
            "judgment_context": (
                expected(
                    "evidence_index",
                    "business_map",
                    "driver_map",
                    "financial_bridge",
                    "scenario_set",
                    *(("valuation_set",) if "valuation_set" in heads else ()),
                    "research_gaps",
                )
            ),
            "memo": expected("judgment_context"),
        }
        if not memo_has_embedded_gaps:
            expectations["research_gaps"] = expected(
                "evidence_index",
                "business_map",
                "driver_map",
                "financial_bridge",
                "scenario_set",
                *(("valuation_set",) if "valuation_set" in heads else ()),
            )
        if "valuation_set" in heads:
            expectations["valuation_set"] = expected("scenario_set", "financial_bridge")
        expected_market_ids: list[str] | None = None
        expected_market_bindings: list[dict[str, object]] | None = None
        for kind, expected_refs in expectations.items():
            row = heads[kind]
            refs, snapshot_ids, bindings = self._validate_closed_lineage_shape(row)
            if refs != expected_refs:
                raise ValidationError(
                    "company research cross-artifact lineage is invalid"
                )
            if expected_market_ids is None:
                expected_market_ids = snapshot_ids
                expected_market_bindings = bindings
            elif (
                snapshot_ids != expected_market_ids
                or bindings != expected_market_bindings
            ):
                raise ValidationError(
                    "company research cross-artifact lineage is invalid"
                )
            parsed_bindings = tuple(
                CompanyResearchRepository.market_binding_from_payload(value)
                for value in bindings
            )
        market_available = heads["judgment_context"].payload.get(
            "market_security_bridge_available"
        )
        if (
            type(market_available) is not bool
            or bool(expected_market_ids) != market_available
            or ("valuation_set" in heads and not expected_market_ids)
        ):
            raise ValidationError("company research cross-artifact lineage is invalid")
        parsed = tuple(
            CompanyResearchRepository.market_binding_from_payload(value)
            for value in (expected_market_bindings or [])
        )
        evidence = heads["evidence_index"]
        evidence_cutoff = self._company.evidence_cutoff(evidence)
        evidence_source_manifest_hash = self._company.evidence_source_manifest_hash(
            evidence
        )
        workspace_boundary = self._company.validate_workspace_market_boundary(
            project_id=project_id,
            bindings=parsed,
            expected_cutoff_at=evidence_cutoff,
            expected_source_manifest_hash=evidence_source_manifest_hash,
            expected_draft_id=expected_draft_id,
            expected_lock_version=expected_draft_lock_version,
        )
        self._company.validate_market_snapshot_bindings(
            project_id=project_id,
            bindings=parsed,
            cutoff_at=workspace_boundary.cutoff_at,
        )
        for kind, expected_refs in expectations.items():
            row = heads[kind]
            refs, _snapshot_ids, bindings = self._validate_closed_lineage_shape(row)
            if refs != expected_refs:
                raise ValidationError(
                    "company research cross-artifact lineage is invalid"
                )
            parsed_bindings = tuple(
                CompanyResearchRepository.market_binding_from_payload(value)
                for value in bindings
            )
            expected_input_hash = self._company._model_artifact_input_hash(
                request_hash=preparation.request_hash,
                artifact_refs=refs,
                historical_basis_id=workspace_boundary.historical_basis_id,
                historical_basis_content_hash=(
                    workspace_boundary.historical_basis_content_hash
                ),
                market_snapshot_bindings=parsed_bindings,
            )
            if row.input_hash != expected_input_hash:
                raise ValidationError(
                    "company research cross-artifact lineage is invalid"
                )
        current_gaps = heads["research_gaps"]
        if memo_has_embedded_gaps:
            if (
                current_gaps.project_id != project_id
                or current_gaps.version != 1
                or current_gaps.supersedes_id is not None
            ):
                raise ValidationError("company research model source refs are invalid")
            governed_source_gaps = current_gaps
        else:
            governed_source_gaps = (
                history_by_id.get(current_gaps.supersedes_id)
                if current_gaps.supersedes_id is not None
                else None
            )
            if (
                current_gaps.project_id != project_id
                or current_gaps.version != 2
                or governed_source_gaps is None
                or governed_source_gaps.project_id != project_id
                or governed_source_gaps.kind != "research_gaps"
                or governed_source_gaps.version != 1
                or governed_source_gaps.supersedes_id is not None
            ):
                raise ValidationError("company research model source refs are invalid")
        expected_source_refs = self._company.expected_model_source_refs(
            evidence=heads["evidence_index"],
            predecessor_gaps=governed_source_gaps,
            market_snapshot_bindings=parsed,
        )
        for kind in expectations:
            row = heads[kind]
            actual = canonical_source_refs(
                row.source_refs,
                field_name=f"{kind} source refs",
            )
            if actual != expected_source_refs or tuple(row.source_refs) != actual:
                raise ValidationError(
                    "company research model source refs do not match governed inputs"
                )
        memo = CompanyResearchArtifactCodec.decode(
            "memo",
            {
                key: value
                for key, value in heads["memo"].payload.items()
                if key != "_lineage"
            },
        )
        judgment = CompanyResearchArtifactCodec.decode(
            "judgment_context",
            {
                key: value
                for key, value in heads["judgment_context"].payload.items()
                if key != "_lineage"
            },
        )
        business_map = CompanyResearchArtifactCodec.decode(
            "business_map",
            {
                key: value
                for key, value in heads["business_map"].payload.items()
                if key != "_lineage"
            },
        )
        derived_gaps = (
            memo.research_gaps
            if memo_has_embedded_gaps
            else CompanyResearchArtifactCodec.decode(
                "research_gaps",
                {
                    key: value
                    for key, value in current_gaps.payload.items()
                    if key != "_lineage"
                },
            )
        )
        assert type(memo) is CompanyResearchMemoArtifact
        assert type(judgment) is JudgmentContextArtifact
        assert type(business_map) is BusinessMapArtifact
        assert isinstance(derived_gaps, tuple) and all(
            type(gap) is ResearchGap for gap in derived_gaps
        )
        has_valuation = "valuation_set" in heads
        validate_company_research_derived_gap_semantics(
            business_map=business_map,
            memo=memo,
            judgment=judgment,
            gaps=derived_gaps,
            has_valuation=has_valuation,
            require_embedded_memo_gaps=memo_has_embedded_gaps,
        )
        memo_refs = {
            "business_map": memo.business_map_ref,
            "driver_map": memo.driver_map_ref,
            "financial_bridge": memo.financial_bridge_ref,
            "scenario_set": memo.scenario_set_ref,
        }
        if any(
            reference.content_hash
            != canonical_hash(
                {
                    key: value
                    for key, value in heads[kind].payload.items()
                    if key != "_lineage"
                }
            )
            for kind, reference in memo_refs.items()
        ):
            raise ValidationError("company research memo references are invalid")
        if has_valuation and (
            memo.valuation_set_ref is None
            or memo.valuation_set_ref.content_hash
            != canonical_hash(
                {
                    key: value
                    for key, value in heads["valuation_set"].payload.items()
                    if key != "_lineage"
                }
            )
        ):
            raise ValidationError("company research memo references are invalid")
        if has_valuation != (memo.valuation_set_ref is not None) or (
            has_valuation and not judgment.market_security_bridge_available
        ):
            raise ValidationError("company research valuation state is invalid")
        if not has_valuation and memo.assessment_status != "not_answerable":
            raise ValidationError("company research valuation state is invalid")
        return governed_source_gaps

    def _heads(
        self,
        project_id: UUID,
        *,
        expected_draft_id: UUID,
        expected_draft_lock_version: int,
        preparation: CompanyResearchPreparation,
        company_external_key: str,
    ) -> tuple[dict[str, WorkbenchArtifact], tuple[dict, ...]]:
        """Materialize all artifact families once; never query once per module."""
        rows = tuple(
            self._session.scalars(
                select(CompanyResearchArtifactVersion)
                .where(CompanyResearchArtifactVersion.project_id == project_id)
                .limit(_MAX_ARTIFACT_HISTORY + 1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if len(rows) > _MAX_ARTIFACT_HISTORY:
            raise ValidationError("company research artifact history limit exceeded")
        by_id = {row.id: row for row in rows}
        successor_ids = {
            row.supersedes_id for row in rows if row.supersedes_id is not None
        }
        head_rows: dict[str, CompanyResearchArtifactVersion] = {}
        evidence_chain: tuple[CompanyResearchArtifactVersion, ...] | None = None
        source_research_gaps: CompanyResearchArtifactVersion | None = None
        for row in rows:
            self._company._validate_artifact_row(row)
            if row.id in successor_ids:
                continue
            if row.kind in head_rows:
                raise ConflictError("multiple current artifact heads")
            # Validate the complete in-memory lineage, including project/kind,
            # monotonic version, and immutable parent content hash.
            current = row
            expected_version = row.version
            seen: set[UUID] = set()
            chain = []
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
                chain.append(current)
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
                if self._company._persisted_utc(
                    current.created_at
                ) < self._company._persisted_utc(parent.created_at):
                    raise ValidationError(
                        "company research artifact timestamps are not monotonic"
                    )
                current, expected_version = parent, expected_version - 1
            if row.kind == "evidence_index":
                evidence_chain = tuple(reversed(chain))
                self._validate_evidence_review_chain(
                    evidence_chain,
                    preparation=preparation,
                    company_external_key=company_external_key,
                )
            elif row.kind == "research_gaps":
                source_research_gaps = chain[-1]
            head_rows[row.kind] = row
        if evidence_chain is not None:
            if source_research_gaps is None:
                raise ValidationError("company research evidence audit history is invalid")
            reconcile_company_research_evidence_audit(
                preparation=preparation,
                evidence_chain=evidence_chain,
                research_gaps=source_research_gaps,
                events=self._company.events(preparation.id, lock=True),
            )
        judgment_head = head_rows.get("judgment_context")
        if judgment_head is not None:
            refs, _snapshot_ids, _bindings = self._validate_closed_lineage_shape(
                judgment_head
            )
            if not any(ref["artifact_kind"] == "valuation_set" for ref in refs):
                head_rows.pop("valuation_set", None)
        self._validate_cross_artifact_lineage(
            project_id,
            head_rows,
            by_id,
            expected_draft_id=expected_draft_id,
            expected_draft_lock_version=expected_draft_lock_version,
        )
        heads = {
            kind: self._artifact(row, project_id) for kind, row in head_rows.items()
        }
        business_map = heads.get("business_map")
        evidence = heads.get("evidence_index")
        if business_map is not None and "_lineage" not in business_map.payload:
            if (
                evidence is None
                or business_map.payload.get("evidence_index_id") != str(evidence.id)
                or business_map.payload.get("evidence_content_hash")
                != evidence.content_hash
            ):
                raise ValidationError("business map input evidence head is invalid")
        current_gap_head = head_rows.get("research_gaps")
        current_gap_values = (
            current_gap_head.payload.get("gaps", [])
            if current_gap_head is not None
            else []
        )
        if not isinstance(current_gap_values, list):
            raise ValidationError("research gaps payload is invalid")
        return heads, tuple(current_gap_values)

    def workspace(self, *, project_id: UUID) -> CompanyResearchWorkspace:
        # This deliberately avoids ``ResearchProjectService.status()``: that
        # projection loads every Security identity one-by-one, while a company
        # workbench needs neither Security identity nor a mutable identity
        # snapshot to render this operational state.
        record = self._product_repository.project(project_id)
        if record is None:
            raise ValidationError("company research project not found")
        project, _security_ids = record
        preparation = self._company.preparation_for_project(
            project_id, fresh=True, lock=True
        )
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
        heads, current_gap_values = self._heads(
            project_id,
            expected_draft_id=draft.id,
            expected_draft_lock_version=draft.lock_version,
            preparation=preparation,
            company_external_key=company_object.external_key,
        )
        modules = []
        for key, kinds in _MODULE_ARTIFACTS.items():
            artifacts = tuple(heads[kind] for kind in kinds if kind in heads)
            if (
                key == "evidence_and_gaps"
                and "evidence_index" in heads
                and preparation.status == "awaiting_evidence_review"
            ):
                state = "needs_review"
            elif len(artifacts) == len(kinds) or (
                key == "scenarios_valuation_implied_expectations"
                and "scenario_set" in heads
                and "judgment_context" in heads
                and "valuation_set" not in heads
            ):
                state: ModuleState = "ready"
            elif preparation.status in {"blocked", "recoverable_failure"}:
                state = "blocked"
            elif preparation.status in {"preparing_sources", "building_model"}:
                state = "preparing"
            else:
                state = "not_started"
            valuation_state = "not_applicable"
            if key == "scenarios_valuation_implied_expectations":
                if "valuation_set" in heads:
                    valuation_state = "ready"
                elif "judgment_context" in heads and "scenario_set" in heads:
                    valuation_state = "blocked"
                else:
                    valuation_state = "pending"
            visible_artifacts = (
                artifacts if state in {"ready", "needs_review"} else ()
            )
            modules.append(
                WorkbenchModule(key, state, visible_artifacts, valuation_state)
            )
        gap_values = current_gap_values
        memo = heads.get("memo")
        if memo is not None and "research_gaps" in memo.payload:
            memo_gap_values = memo.payload["research_gaps"]
            if not isinstance(memo_gap_values, list):
                raise ValidationError("memo research gaps payload is invalid")
            gap_values = memo_gap_values
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
        error = None
        if preparation.status in {"blocked", "recoverable_failure"}:
            if preparation.last_error_code is None or preparation.current_step is None:
                raise ValidationError(
                    "failed company research preparation requires typed error semantics"
                )
            error = WorkbenchPreparationError(
                preparation.last_error_code,
                preparation.current_step,
                preparation.status == "recoverable_failure",
                preparation.next_attempt_at,
            )
        elif preparation.last_error_code is not None:
            raise ValidationError(
                "non-failed company research preparation cannot expose an error"
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
                preparation.strategy_version,
                preparation.status,
                preparation.current_step,
                preparation.progress,
                error,
            ),
            tuple(modules),
            tuple(heads[kind] for kind in _ARTIFACT_ORDER if kind in heads),
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
                "model_bundle",
                25,
                None,
            )
            preparation.status, preparation.current_step, preparation.progress = (
                "building_model",
                "model_bundle",
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
