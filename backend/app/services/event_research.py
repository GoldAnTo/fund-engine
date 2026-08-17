"""Creation service for independent, automatically-starting event research."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.research_preparation import preparation_input_fingerprint
from app.models.event_research import (
    EventResearchBrief,
    EventResearchFactorDraft,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.operational import EventResearchLifecycle
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.ingest import DocumentService
from app.services.document_uploads import DocumentUploadService
from app.services.research import ResearchService
from app.services.research_preparation import ResearchPreparationService
from app.services.source_governance import SourceGovernanceService
from app.services.case_tenant_access import CaseTenantAccess
from app.services.auto_research import AutoResearchService
from app.errors import ValidationFailedError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _submitted_snapshot_natural_key(namespace: str, raw_input: str) -> str:
    """Deduplicate byte-identical submissions without collapsing new material.

    Generic event:// locations and titles describe an intake channel, not a
    semantic source document.  They must therefore not share the regular
    source/title/date natural key used for issuer reports.
    """
    digest = hashlib.sha256(raw_input.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


@dataclass(frozen=True)
class CreatedEventResearch:
    case_id: str
    brief_id: str
    lifecycle: EventResearchLifecycle
    run_id: str | None
    preparation_id: str | None


@dataclass(frozen=True)
class InitialUploadedOriginal:
    raw: bytes
    file_name: str
    mime_type: str
    source_metadata: dict


class EventResearchService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, payload: CreateEventResearchRequest, *, tenant_id: str,
        initial_uploaded_original: InitialUploadedOriginal | None = None,
        workflow_mode: str = "reviewed",
    ) -> CreatedEventResearch:
        if workflow_mode not in {"reviewed", "automatic"}:
            raise ValueError("workflow_mode must be 'reviewed' or 'automatic'")
        research = ResearchService(ResearchRepository(self._session))
        case = research.add_case(
            title=payload.event_title,
            industry_topic="事件研究",
            created_by=payload.created_by,
            research_object=payload.company_name or payload.event_title,
            phenomenon=payload.market_reaction,
            core_question=payload.research_question,
            evidence_cutoff=payload.event_at.date() if payload.event_at else None,
        )
        document_service = DocumentService(DocumentRepository(self._session))
        document = None
        if initial_uploaded_original is None:
            document_url = payload.source_url or {
                "pasted_snapshot": "event://pasted-news",
                "uploaded_file": "upload://event-text-snapshot",
                "licensed_provider": "provider://unresolved-record",
                "public_url": "https://invalid.example/public-url-required",
            }[payload.source_type]
            document = document_service.freeze(
                raw=payload.raw_input.encode("utf-8"),
                source_url=document_url,
                parser_version={"pasted_snapshot": "user-pasted-v1", "uploaded_file": "uploaded-text-v1", "licensed_provider": "provider-snapshot-v1", "public_url": "user-pasted-public-url-v1"}[payload.source_type],
                title=payload.event_title,
                parse_state="partial",
                source_authority=payload.source_metadata.get("authority_level", "unknown"),
            )
            document_service.attach_to_case(
                research_case_id=case.id, document_version_id=document.id
            )
            SourceGovernanceService(self._session).record_event_intake(
                document=document,
                source_type=payload.source_type,
                source_metadata=payload.source_metadata,
                declared_by=payload.created_by,
                incoming_source_url=payload.source_url,
            )
            document_service.add_span(
                document_version_id=document.id,
                locator={"kind": payload.source_type, "source_metadata": payload.source_metadata},
                verbatim_text=payload.raw_input,
        )
        now = _utcnow()
        brief = EventResearchBrief(
            research_case_id=case.id,
            raw_input=payload.raw_input,
            source_url=payload.source_url,
            source_type=payload.source_type,
            source_metadata=payload.source_metadata,
            event_title=payload.event_title,
            company_name=payload.company_name,
            ticker=payload.ticker,
            event_at=payload.event_at,
            market_reaction=payload.market_reaction,
            research_question=payload.research_question,
            workflow_mode=workflow_mode,
            extraction_state=(
                "human_confirmed"
                if workflow_mode == "reviewed"
                else "system_generated"
            ),
            created_at=now,
        )
        self._session.add(brief)
        scope = EventResearchScopeVersion(
            research_case_id=case.id,
            version=1,
            changed_by=payload.created_by,
            change_summary="Initial event research factors",
            created_at=now,
        )
        self._session.add(scope)
        self._session.flush()
        theses = []
        for position, factor in enumerate(payload.candidate_factors, start=1):
            statement = factor.strip()
            self._session.add(
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=statement,
                    description=None,
                    position=position,
                )
            )
            self._session.add(
                EventResearchFactorDraft(
                    research_case_id=case.id,
                    statement=statement,
                    position=position,
                    created_by="human",
                    created_at=now,
                )
            )
            theses.append(
                research.add_thesis(
                    case.id,
                    statement=statement,
                    created_by=payload.created_by,
                    creator_type="human" if workflow_mode == "reviewed" else "ai",
                    review_state="confirmed" if workflow_mode == "reviewed" else "draft",
                    research_protocol_required=payload.research_protocol_required,
                )
            )
        self._session.flush()

        try:
            lifecycle = None
            preparation = None
            run = None
            if initial_uploaded_original is not None:
                # The upload service deliberately requires an event lifecycle.
                # It is staged in this uncommitted transaction, before the
                # original is frozen and before any preparation job is queued.
                lifecycle = EventResearchLifecycle(
                    research_case_id=case.id,
                    status="awaiting_key_review",
                    active_run_id=None,
                    current_round=0,
                    status_summary="资料已冻结；系统正在准备候选陈述、研究协议草案和补证计划",
                    current_gap="研究准备尚未完成；ResearchRun 未创建，正式补证尚未启动",
                    next_human_action=None,
                    updated_at=_utcnow(),
                )
                self._session.add(lifecycle)
                self._session.flush()
                uploaded = DocumentUploadService(self._session).freeze_case_material(
                    case_id=case.id,
                    raw=initial_uploaded_original.raw,
                    file_name=initial_uploaded_original.file_name,
                    mime_type=initial_uploaded_original.mime_type,
                    actor=payload.created_by,
                    source_metadata=initial_uploaded_original.source_metadata,
                )
                document = uploaded.document
            assert document is not None
            CaseTenantAccess(self._session).admit_initial_case(
                case_id=case.id,
                tenant_id=tenant_id,
                initial_document_version_id=document.id,
                admitted_by=payload.created_by,
            )
            if workflow_mode == "reviewed":
                # Preparation only schedules the source-bound draft workflow. It
                # never authorizes collection or creates a formal ResearchRun.
                preparation = ResearchPreparationService(
                    self._session
                ).create_for_case(
                    case.id,
                    input_fingerprint=preparation_input_fingerprint(
                        document.id, scope.id
                    ),
                    actor=payload.created_by,
                )
                if lifecycle is None:
                    lifecycle = EventResearchLifecycle(
                        research_case_id=case.id,
                        status="awaiting_key_review",
                        active_run_id=None,
                        current_round=0,
                        status_summary="资料已冻结；系统正在准备候选陈述、研究协议草案和补证计划",
                        current_gap="研究准备尚未完成；ResearchRun 未创建，正式补证尚未启动",
                        next_human_action=None,
                        updated_at=_utcnow(),
                    )
                    self._session.add(lifecycle)
            else:
                factors = [thesis.statement for thesis in theses]
                run = AutoResearchService(self._session).start(
                    case.id,
                    max_rounds=3,
                    budget=100,
                    thesis_ids=[thesis.id for thesis in theses],
                    trigger="automatic_intake",
                    commit=False,
                    scope_context={
                        "workflow_mode": "automatic",
                        "automatic_protocol": {
                            "generated_by": "system",
                            "research_question": payload.research_question,
                            "factors": factors,
                            "conclusion_rule": (
                                "report support, contradiction, and "
                                "insufficiency separately"
                            ),
                        },
                        "automatic_evidence_plan": {
                            "items": [
                                {
                                    "factor": factor,
                                    "objectives": [
                                        "support",
                                        "contradict",
                                        "alternative_explanation",
                                    ],
                                    "allowed_source_roles": [
                                        "company_disclosure",
                                        "licensed_provider",
                                    ],
                                }
                                for factor in factors
                            ],
                            "max_rounds": 3,
                            "budget": 100,
                        },
                    },
                )
                lifecycle = EventResearchLifecycle(
                    research_case_id=case.id,
                    status="researching",
                    active_run_id=run.id,
                    current_round=1,
                    status_summary="自动研究已排队",
                    current_gap=None,
                    next_human_action=None,
                    updated_at=_utcnow(),
                )
                self._session.add(lifecycle)
            self._session.commit()
        except Exception:
            # Preparation is part of event intake's one unit of work.  In
            # particular, a failed job enqueue must not leave a half-created
            # Case, frozen document, or tenant admission behind.
            self._session.rollback()
            raise
        return CreatedEventResearch(
            case_id=str(case.id),
            brief_id=str(brief.id),
            lifecycle=lifecycle,
            run_id=str(run.id) if run is not None else None,
            preparation_id=(
                str(preparation.id) if preparation is not None else None
            ),
        )

    def freeze_published_material(
        self,
        case_id,
        *,
        raw_input: str,
        source_url: str | None,
        source_type: str,
        source_metadata: dict,
        actor: str,
    ):
        """Attach a newly submitted, immutable material snapshot to a published Case.

        The caller must still make an explicit post-intake decision.  Freezing
        and attaching material is deliberately not a lifecycle transition.
        """
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        if lifecycle is None or lifecycle.status != "published":
            raise ValidationFailedError("only a published event research case can accept new material")
        document_service = DocumentService(DocumentRepository(self._session))
        document_url = source_url or {
            "pasted_snapshot": "event://published-material-snapshot",
            "uploaded_file": "upload://published-material-text-snapshot",
            "licensed_provider": "provider://unresolved-record",
            "public_url": "https://invalid.example/public-url-required",
        }[source_type]
        document = document_service.freeze(
            raw=raw_input.encode("utf-8"),
            source_url=document_url,
            parser_version={"pasted_snapshot": "user-pasted-v1", "uploaded_file": "uploaded-text-v1", "licensed_provider": "provider-snapshot-v1", "public_url": "user-pasted-public-url-v1"}[source_type],
            title="已发布 Case 的新增材料",
            natural_key=_submitted_snapshot_natural_key(
                "published-material", raw_input
            ),
            parse_state="partial",
            source_authority=source_metadata.get("authority_level", "unknown"),
        )
        document_service.attach_to_case(research_case_id=case_id, document_version_id=document.id)
        SourceGovernanceService(self._session).record_event_intake(
            document=document,
            source_type=source_type,
            source_metadata=source_metadata,
            declared_by=actor,
            incoming_source_url=source_url,
        )
        document_service.add_span(
            document_version_id=document.id,
            locator={"kind": source_type, "source_metadata": source_metadata, "intake": "published_material"},
            verbatim_text=raw_input,
        )
        return document

    def attach_material_to_existing_case(
        self,
        case_id,
        *,
        raw_input: str,
        source_url: str | None,
        source_type: str,
        source_metadata: dict,
        actor: str,
    ):
        """Freeze a new inbox item in an existing, non-published Case.

        A published Case must use the explicit comparison/decision workflow;
        intake alone never changes a lifecycle or creates a ResearchRun.
        """
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        if lifecycle is None:
            raise ValidationFailedError("event research case not found")
        if lifecycle.status == "published":
            raise ValidationFailedError(
                "published case material must use the published-material decision workflow"
            )
        document_service = DocumentService(DocumentRepository(self._session))
        document_url = source_url or {
            "pasted_snapshot": "event://inbox-material-snapshot",
            "uploaded_file": "upload://inbox-material-text-snapshot",
            "licensed_provider": "provider://unresolved-record",
            "public_url": "https://invalid.example/public-url-required",
        }[source_type]
        document = document_service.freeze(
            raw=raw_input.encode("utf-8"),
            source_url=document_url,
            parser_version={
                "pasted_snapshot": "user-pasted-v1",
                "uploaded_file": "uploaded-text-v1",
                "licensed_provider": "provider-snapshot-v1",
                "public_url": "user-pasted-public-url-v1",
            }[source_type],
            title=source_metadata.get("file_name", "收件箱新增材料"),
            natural_key=_submitted_snapshot_natural_key(
                "existing-case-material", raw_input
            ),
            parse_state="partial",
            source_authority=source_metadata.get("authority_level", "unknown"),
        )
        document_service.attach_to_case(
            research_case_id=case_id, document_version_id=document.id
        )
        SourceGovernanceService(self._session).record_event_intake(
            document=document,
            source_type=source_type,
            source_metadata=source_metadata,
            declared_by=actor,
            incoming_source_url=source_url,
        )
        document_service.add_span(
            document_version_id=document.id,
            locator={"kind": source_type, "source_metadata": source_metadata, "intake": "existing_case"},
            verbatim_text=raw_input,
        )
        return document
