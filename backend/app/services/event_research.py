"""Creation service for independent, automatically-starting event research."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

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
from app.services.research import ResearchService
from app.services.source_governance import SourceGovernanceService
from app.services.case_tenant_access import CaseTenantAccess
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


class EventResearchService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, payload: CreateEventResearchRequest, *, tenant_id: str
    ) -> CreatedEventResearch:
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
        CaseTenantAccess(self._session).admit_initial_case(
            case_id=case.id,
            tenant_id=tenant_id,
            initial_document_version_id=document.id,
            admitted_by=payload.created_by,
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
            extraction_state="human_confirmed",
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
            research.add_thesis(
                case.id,
                statement=statement,
                created_by=payload.created_by,
                creator_type="human",
                review_state="confirmed",
                research_protocol_required=payload.research_protocol_required,
            )
        self._session.flush()

        # Intake freezes a source snapshot and a researcher-proposed scope;
        # it is deliberately not authorization to run collection or model
        # work. The original material must be inspected and the Case protocol
        # completed before a separately configured ResearchRun can exist.
        lifecycle = EventResearchLifecycle(
            research_case_id=case.id,
            status="awaiting_key_review",
            active_run_id=None,
            current_round=0,
            status_summary="资料已冻结，等待核验原文与研究协议；尚未启动后台研究",
            current_gap="原文资料、来源许可与研究协议尚未完成核验",
            next_human_action="核验原文资料并完成研究协议",
            updated_at=_utcnow(),
        )
        self._session.add(lifecycle)
        self._session.commit()
        return CreatedEventResearch(
            case_id=str(case.id), brief_id=str(brief.id), lifecycle=lifecycle
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
