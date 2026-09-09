"""Real-provider execution and immutable language-model research receipts."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.ai.usage import capture_usage, current_usage
from app.models.ledger import AIRun, ValidationError
from app.underwriting.domain.company_research import CompanyResearchDraftReference
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchDraft,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.models import UnderwritingHistoricalBasis
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_progress import (
    company_research_product_progress,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService


def source_storage_root() -> Path:
    return Path(
        os.environ.get("COMPANY_RESEARCH_SOURCE_DIR", "data/company-research-sources")
    ).resolve()


def live_research_context(
    session,
    preparation: CompanyResearchPreparation,
    *,
    frozen_scope_id: UUID | None = None,
    frozen_basis_id: UUID | None = None,
) -> dict:
    products = ProductRepository(session)
    record = products.project(preparation.project_id)
    if record is None:
        raise ValidationError("company research context is missing")
    if frozen_scope_id is not None or frozen_basis_id is not None:
        if frozen_scope_id is None or frozen_basis_id is None:
            raise ValidationError("frozen company research context is incomplete")
        scope_id, basis_id = frozen_scope_id, frozen_basis_id
    else:
        draft = WorkspaceDraftService(session, now=lambda: datetime.now(UTC)).read(
            preparation.project_id
        )
        if draft is None:
            raise ValidationError("company research context is missing")
        scope_id, basis_id = draft.content.scope_id, draft.content.historical_basis_id
    project, _ = record
    company = products.object(project.primary_company_id)
    scope = products.scope(project.id, scope_id)
    basis = session.get(UnderwritingHistoricalBasis, basis_id) if basis_id else None
    progress = company_research_product_progress(
        session=session,
        preparation=preparation,
        company_id=project.primary_company_id,
        scope=scope,
        basis=basis,
    )
    if company is None or progress is None:
        raise ValidationError("company research context is incomplete")
    return {
        "project_id": project.id,
        "preparation_id": preparation.id,
        "request_hash": preparation.request_hash,
        "strategy_version": preparation.strategy_version,
        "user_focus": progress.user_focus,
        "cutoff_at": progress.cutoff_at,
        "company_name": company.canonical_name,
    }


@dataclass(frozen=True)
class CompanyResearchLiveExecution:
    context: dict
    source_bundle: dict | None
    generation: Any | None
    model_version: str
    prompt_version: str
    usage: dict | None
    started_at: datetime
    finished_at: datetime
    error_code: str | None = None
    input_hash: str | None = None


class CompanyResearchLiveRuntime:
    def __init__(self, *, generator, storage_root: Path, now, capture=None):
        self.generator = generator
        self.storage_root = storage_root
        self.now = now
        self.capture = capture

    @classmethod
    def from_env(cls, *, now):
        from app.ai.client import LLMClient
        from app.ai.company_research import CompanyResearchDraftGenerator

        if os.getenv("APP_ENV", "").lower() == "test":
            raise ValidationError("live company research cannot use the test runtime")
        client = LLMClient.from_env()
        if client._mock:
            raise ValidationError("live company research requires a real provider")
        return cls(
            generator=CompanyResearchDraftGenerator(client),
            storage_root=source_storage_root(),
            now=now,
        )

    def execute(self, context: dict, compiled) -> CompanyResearchLiveExecution:
        from app.ai.company_research import PROMPT_VERSION
        from app.underwriting.services.company_research_live_sources import (
            capture_governed_alphabet_sources,
        )

        started = self.now()
        bundle = None
        generation = None
        error_code = None
        input_hash = None
        model = "not-invoked"
        with capture_usage():
            try:
                captured = (self.capture or capture_governed_alphabet_sources)(
                    compiled.evidence_index_payload,
                    compiled.source_refs,
                    now=self.now,
                    storage_root=self.storage_root,
                )
                bundle = captured.payload
                input_hash = self.generator.input_hash_for(
                    **context,
                    source_bundle=bundle,
                    evidence_payload=compiled.evidence_index_payload,
                )
                # The generator is the only component allowed to call a language model.
                generation = self.generator.generate(
                    **context,
                    source_bundle=bundle,
                    evidence_payload=compiled.evidence_index_payload,
                )
                model = generation.model_version
            except Exception:  # noqa: BLE001 -- every failed external attempt needs a safe audit receipt
                # Never include upstream errors, prompts or credentials in logs or API errors.
                error_code = (
                    "company_research_live_generation_failed"
                    if bundle is not None
                    else "company_research_live_source_failed"
                )
                model = (
                    getattr(self.generator, "model_version", "provider-not-reported")
                    if bundle is not None
                    else "not-invoked"
                )
            usage = current_usage()
        return CompanyResearchLiveExecution(
            deepcopy(context),
            bundle,
            generation,
            model,
            PROMPT_VERSION,
            usage,
            started,
            self.now(),
            error_code,
            input_hash,
        )


def record_live_execution(session, execution: CompanyResearchLiveExecution) -> UUID:
    context = execution.context
    generation = execution.generation
    source_hash = (
        execution.source_bundle.get("bundle_hash") if execution.source_bundle else None
    )
    audit = AIRun(
        id=uuid4(),
        kind="company_research_draft",
        model_version=execution.model_version,
        prompt_version=execution.prompt_version,
        input_ref={
            "company_research_project_id": str(context["project_id"]),
            "company_research_preparation_id": str(context["preparation_id"]),
            "request_hash": context["request_hash"],
            "source_bundle_hash": source_hash,
            "input_hash": generation.input_hash if generation else execution.input_hash,
            "source_bundle": deepcopy(execution.source_bundle),
            "focus_hash": canonical_hash(context["user_focus"]),
        },
        output_summary=json.dumps(
            {
                "output_hash": generation.output_hash if generation else None,
                "source_bundle_hash": source_hash,
            },
            sort_keys=True,
        ),
        status="success"
        if generation is not None and execution.error_code is None
        else "failed",
        error=execution.error_code,
        usage=execution.usage,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
    )
    session.add(audit)
    session.flush()
    return audit.id


def persist_live_draft(
    session,
    execution: CompanyResearchLiveExecution,
    *,
    ai_run_id: UUID,
    evidence_artifact_id: UUID,
) -> CompanyResearchDraft:
    if (
        execution.error_code is not None
        or execution.generation is None
        or execution.source_bundle is None
    ):
        raise ValidationError("failed research generation cannot create a draft")
    context, generation = execution.context, execution.generation
    draft_id = uuid4()
    payload = {
        "schema_version": "company-research.research-draft.v1",
        "id": str(draft_id),
        "project_id": str(context["project_id"]),
        "preparation_id": str(context["preparation_id"]),
        "request_hash": context["request_hash"],
        "strategy_version": context["strategy_version"],
        "user_focus": context["user_focus"],
        "cutoff_at": context["cutoff_at"].isoformat(),
        "company_name": context["company_name"],
        "source_bundle": deepcopy(execution.source_bundle),
        "generation": deepcopy(generation.payload),
        "markdown": generation.markdown,
        "input_hash": generation.input_hash,
        "output_hash": generation.output_hash,
        "model_version": generation.model_version,
        "prompt_version": generation.prompt_version,
        "ai_run_id": str(ai_run_id),
        "evidence_artifact_id": str(evidence_artifact_id),
        "created_at": execution.finished_at.isoformat(),
    }
    row = CompanyResearchDraft(
        id=draft_id,
        project_id=context["project_id"],
        preparation_id=context["preparation_id"],
        ai_run_id=ai_run_id,
        payload=payload,
        content_hash=canonical_hash(payload),
        created_at=execution.finished_at,
    )
    session.add(row)
    session.flush()
    return row


def read_live_draft(
    session,
    *,
    project_id: UUID,
    preparation=None,
    verify_raw: bool = False,
    frozen_scope_id: UUID | None = None,
    frozen_basis_id: UUID | None = None,
) -> CompanyResearchDraft | None:
    from app.underwriting.services.company_research_live_sources import (
        validate_frozen_company_sources,
    )

    row = session.scalar(
        select(CompanyResearchDraft)
        .where(CompanyResearchDraft.project_id == project_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    value = row.payload
    expected_keys = {
        "schema_version",
        "id",
        "project_id",
        "preparation_id",
        "request_hash",
        "strategy_version",
        "user_focus",
        "cutoff_at",
        "company_name",
        "source_bundle",
        "generation",
        "markdown",
        "input_hash",
        "output_hash",
        "model_version",
        "prompt_version",
        "ai_run_id",
        "evidence_artifact_id",
        "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema_version") != "company-research.research-draft.v1"
        or canonical_hash(value) != row.content_hash
        or value.get("id") != str(row.id)
        or value.get("project_id") != str(project_id)
    ):
        raise ValidationError("company research draft integrity check failed")
    prep = preparation or session.get(CompanyResearchPreparation, row.preparation_id)
    if (
        prep is None
        or prep.id != row.preparation_id
        or prep.project_id != project_id
        or value.get("preparation_id") != str(prep.id)
        or value.get("request_hash") != prep.request_hash
    ):
        raise ValidationError("company research draft run binding is invalid")
    context = live_research_context(
        session, prep, frozen_scope_id=frozen_scope_id, frozen_basis_id=frozen_basis_id
    )
    if (
        value.get("user_focus") != context["user_focus"]
        or value.get("cutoff_at") != context["cutoff_at"].isoformat()
        or value.get("strategy_version") != prep.strategy_version
    ):
        raise ValidationError("company research draft scope binding is invalid")
    audit = session.get(AIRun, row.ai_run_id, populate_existing=True)
    bundle = value.get("source_bundle")
    generation = value.get("generation")
    if (
        audit is None
        or audit.status != "success"
        or audit.kind != "company_research_draft"
        or value.get("ai_run_id") != str(audit.id)
        or audit.input_ref.get("company_research_project_id") != str(project_id)
        or audit.input_ref.get("company_research_preparation_id") != str(prep.id)
        or audit.input_ref.get("request_hash") != prep.request_hash
        or audit.input_ref.get("input_hash") != value.get("input_hash")
        or audit.input_ref.get("focus_hash") != canonical_hash(context["user_focus"])
        or audit.model_version != value.get("model_version")
        or audit.prompt_version != value.get("prompt_version")
        or not isinstance(bundle, dict)
        or not isinstance(generation, dict)
    ):
        raise ValidationError("company research draft provider receipt is invalid")
    if (
        audit.output_summary
        != json.dumps(
            {
                "output_hash": value.get("output_hash"),
                "source_bundle_hash": bundle.get("bundle_hash"),
            },
            sort_keys=True,
        )
        or audit.input_ref.get("source_bundle_hash") != bundle.get("bundle_hash")
        or audit.input_ref.get("source_bundle") != bundle
        or generation.get("source_bundle_hash") != bundle.get("bundle_hash")
    ):
        raise ValidationError("company research draft source binding is invalid")
    if any(
        generation.get(key) != value.get(key)
        for key in ("user_focus", "cutoff_at", "company_name")
    ) or generation.get("evidence_payload_hash") != bundle.get("evidence_payload_hash"):
        raise ValidationError("company research draft generation context is invalid")
    validate_frozen_company_sources(
        bundle, storage_root=source_storage_root(), verify_raw=verify_raw
    )
    try:
        evidence = session.get(
            CompanyResearchArtifactVersion, UUID(value["evidence_artifact_id"]), populate_existing=True
        )
    except (TypeError, ValueError):
        raise ValidationError(
            "company research draft evidence reference is invalid"
        ) from None
    if (
        evidence is None
        or evidence.kind != "evidence_index"
        or evidence.version != 1
        or evidence.project_id != project_id
        or canonical_hash(evidence.payload) != bundle.get("evidence_payload_hash")
        or evidence.input_hash != bundle.get("governed_manifest_hash")
    ):
        raise ValidationError("company research draft evidence binding is invalid")
    from app.underwriting.persistence.company_research_repository import (
        CompanyResearchRepository,
    )

    CompanyResearchRepository._validate_artifact_row(evidence)
    from app.ai.company_research import (
        CompanyResearchDraftInputError,
        validate_saved_generation,
    )

    try:
        computed_output_hash = validate_saved_generation(
            generation,
            value["markdown"],
            source_bundle=bundle,
            evidence_payload=evidence.payload,
            model_version=value["model_version"],
        )
    except CompanyResearchDraftInputError:
        raise ValidationError(
            "company research draft content validation failed"
        ) from None
    if computed_output_hash != value["output_hash"]:
        raise ValidationError("company research draft output hash is invalid")
    return row


def live_draft_reference(row: CompanyResearchDraft) -> CompanyResearchDraftReference:
    return CompanyResearchDraftReference(
        row.id, row.content_hash, row.payload["source_bundle"]["bundle_hash"]
    )


def validate_memo_research_draft(
    session,
    *,
    project_id: UUID,
    reference: CompanyResearchDraftReference | None,
    verify_raw: bool = False,
    frozen_scope_id: UUID | None = None,
    frozen_basis_id: UUID | None = None,
):
    row = read_live_draft(
        session,
        project_id=project_id,
        verify_raw=verify_raw,
        frozen_scope_id=frozen_scope_id,
        frozen_basis_id=frozen_basis_id,
    )
    expected = live_draft_reference(row) if row is not None else None
    if reference != expected:
        raise ValidationError(
            "company research memo does not bind its exact research draft"
        )
    return row


def live_draft_projection(session, row: CompanyResearchDraft | None) -> dict | None:
    if row is None:
        return None
    value = row.payload
    audit = session.get(AIRun, row.ai_run_id)
    labels = {
        "business_analysis": "商业分析",
        "operating_drivers": "经营驱动",
        "candidate_assumptions": "候选假设",
        "counterevidence": "反证与风险",
        "verification_questions": "待核验问题",
        "report_sections": "研究报告",
    }
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "preparation_id": str(row.preparation_id),
        "request_hash": value["request_hash"],
        "content_hash": row.content_hash,
        "source_bundle_hash": value["source_bundle"]["bundle_hash"],
        "input_hash": value["input_hash"],
        "output_hash": value["output_hash"],
        "candidate_status": "machine_draft",
        "user_focus": value["user_focus"],
        "cutoff_at": value["cutoff_at"],
        "model_version": value["model_version"],
        "prompt_version": value["prompt_version"],
        "generated_at": value["created_at"],
        "markdown": value["markdown"],
        "sections": [
            {"key": key, "label": label, "items": value["generation"][key]}
            for key, label in labels.items()
        ],
        "sources": [
            {
                key: source[key]
                for key in (
                    "source_id",
                    "source_url",
                    "raw_hash",
                    "available_at",
                    "retrieved_at",
                )
            }
            for source in value["source_bundle"]["documents"]
        ],
        "usage": audit.usage,
    }
