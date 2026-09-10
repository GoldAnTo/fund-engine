"""Authenticated application read model for one company-research run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    DEFAULT_STRATEGY_VERSION,
    LEGACY_STRATEGY_VERSION,
    CompanyResearchCompany,
    CompanyResearchSecurity,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.domain.company_research_critical_inputs import CriticalInputSet
from app.underwriting.domain.company_research_run import (
    ResearchRunPreparation,
    ResearchRunProjectionError,
    ResearchRunStage,
    ResearchRunStatus,
    project_research_run,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_foundation import (
    build_company_research_preview_at_cutoff,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchFrozenRevision,
    CompanyResearchPublicationService,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
    CompanyResearchWorkspace,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService

_RECENT_PROCESS_LIMIT = 3
_FULL_PROCESS_LIMIT = 100


@dataclass(frozen=True, slots=True)
class CompanyResearchRunRetry:
    retryable: bool
    next_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class CompanyResearchRunProcessEntry:
    code: str
    message: str
    occurred_at: datetime
    retry: CompanyResearchRunRetry | None


@dataclass(frozen=True, slots=True)
class CompanyResearchRunCriticalInputs:
    artifact_id: UUID
    version: int
    input_hash: str
    content_hash: str
    value: CriticalInputSet


@dataclass(frozen=True, slots=True)
class CompanyResearchRun:
    project_id: UUID
    company: CompanyResearchCompany
    securities: tuple[CompanyResearchSecurity, ...]
    status: ResearchRunStatus
    progress: int
    started_at: datetime
    updated_at: datetime
    stages: tuple[ResearchRunStage, ...]
    recent_process: tuple[CompanyResearchRunProcessEntry, ...]
    workspace: CompanyResearchWorkspace
    critical_inputs: CompanyResearchRunCriticalInputs | None
    selected_revision: UUID | None


@dataclass(frozen=True, slots=True)
class AuthenticatedCompanyResearchWorkspace:
    workspace: CompanyResearchWorkspace
    frozen_revision: CompanyResearchFrozenRevision | None


def _stored_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"company research {field_name} is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _security_identity(
    value: CompanyResearchSecurity,
) -> tuple[UUID, UUID, str, str, str, str, str, str]:
    return (
        value.object_id,
        value.company_id,
        value.external_key,
        value.canonical_name,
        value.symbol,
        value.exchange,
        value.share_class,
        value.trading_currency,
    )


def authenticated_company_research_workspace(
    session, *, project_id: UUID, now: Callable[[], datetime]
) -> AuthenticatedCompanyResearchWorkspace:
    """Authenticate current heads and any selected frozen revision."""

    value = CompanyResearchWorkbench(session, now=now).workspace(
        project_id=project_id,
        allow_current_heads_after_revision=True,
    )
    if value.selected_revision is None:
        return AuthenticatedCompanyResearchWorkspace(value, None)
    frozen = CompanyResearchPublicationService(session, now=now).revision(
        project_id, value.selected_revision
    )
    current_descriptors = tuple(
        (item.kind, item.id, item.version, item.input_hash, item.content_hash)
        for item in value.artifacts
    )
    frozen_descriptors = tuple(
        (item.kind, item.id, item.version, item.input_hash, item.content_hash)
        for item in frozen.artifacts
        if item.kind != "critical_inputs"
    )
    frozen_critical = tuple(
        (item.kind, item.id, item.version, item.input_hash, item.content_hash)
        for item in frozen.artifacts
        if item.kind == "critical_inputs"
    )
    current_critical = CompanyResearchRepository(session).current_artifact(
        project_id, "critical_inputs"
    )
    current_critical_descriptor = (
        (
            (
                current_critical.kind,
                current_critical.id,
                current_critical.version,
                current_critical.input_hash,
                current_critical.content_hash,
            ),
        )
        if current_critical is not None
        else ()
    )
    if (
        value.selected_revision != frozen.id
        or current_descriptors != frozen_descriptors
        or current_critical_descriptor != frozen_critical
        or (
            value.company.id,
            value.company.external_key,
            value.company.canonical_name,
        )
        != (
            frozen.company.object_id,
            frozen.company.external_key,
            frozen.company.canonical_name,
        )
    ):
        raise ValidationError(
            "company research workspace differs from its selected frozen revision"
        )
    return AuthenticatedCompanyResearchWorkspace(value, frozen)


class CompanyResearchRunService:
    """Compose existing authenticated reads into the stable product run."""

    def __init__(self, session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._company = CompanyResearchRepository(session)
        self._product = ProductRepository(session)

    def _identities(
        self,
        workspace: CompanyResearchWorkspace,
        *,
        frozen_revision: CompanyResearchFrozenRevision | None,
    ) -> tuple[CompanyResearchCompany, tuple[CompanyResearchSecurity, ...]]:
        draft = WorkspaceDraftService(self._session, now=self._now).read(
            workspace.project_id
        )
        if draft is None or draft.id != workspace.draft.id:
            raise ValidationError("company research workspace draft is inconsistent")
        if draft.content.historical_basis_id is None:
            raise ValidationError("company research historical basis is missing")
        basis = self._product.product_basis(draft.content.historical_basis_id)
        if basis is None:
            raise ValidationError("company research historical basis is missing")
        cutoff = _stored_utc(basis.cutoff, "historical basis cutoff")
        preview = build_company_research_preview_at_cutoff(
            self._session,
            company_id=workspace.company.id,
            cutoff_at=cutoff,
            strategy_version=workspace.preparation.strategy_version,
        )
        project = self._product.project(workspace.project_id)
        if project is None:
            raise ValidationError("company research project not found")
        project_row, security_ids = project
        preview_security_ids = tuple(
            sorted((item.object_id for item in preview.securities), key=str)
        )
        if (
            project_row.primary_company_id != workspace.company.id
            or preview.company.object_id != workspace.company.id
            or preview.company.external_key != workspace.company.external_key
            or preview.company.canonical_name != workspace.company.canonical_name
            or tuple(sorted(security_ids, key=str)) != preview_security_ids
            or any(
                item.company_id != workspace.company.id for item in preview.securities
            )
        ):
            raise ValidationError("company research run identity is inconsistent")
        if (workspace.selected_revision is None) != (frozen_revision is None):
            raise ValidationError(
                "company research workspace differs from its selected frozen revision"
            )
        if frozen_revision is not None:
            if sorted(
                (_security_identity(item) for item in preview.securities),
                key=lambda item: str(item[0]),
            ) != sorted(
                (_security_identity(item) for item in frozen_revision.securities),
                key=lambda item: str(item[0]),
            ):
                raise ValidationError(
                    "company research workspace differs from its selected frozen revision"
                )
            return frozen_revision.company, frozen_revision.securities
        return preview.company, preview.securities

    def _critical_inputs(
        self, workspace: CompanyResearchWorkspace, *, has_machine_memo: bool
    ) -> CompanyResearchRunCriticalInputs | None:
        head = self._company.current_artifact(workspace.project_id, "critical_inputs")
        if head is None:
            if (
                workspace.preparation.strategy_version == DEFAULT_STRATEGY_VERSION
                and has_machine_memo
            ):
                raise ValidationError(
                    "mainline company research machine draft requires critical inputs"
                )
            return None
        if workspace.preparation.strategy_version == LEGACY_STRATEGY_VERSION:
            raise ValidationError(
                "legacy company research cannot expose mainline critical inputs"
            )
        decoded = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
        if type(decoded) is not CriticalInputSet:
            raise ValidationError("critical_inputs payload is invalid")
        return CompanyResearchRunCriticalInputs(
            artifact_id=head.id,
            version=head.version,
            input_hash=head.input_hash,
            content_hash=head.content_hash,
            value=decoded,
        )

    @staticmethod
    def _process(
        *,
        projection,
        events,
        workspace: CompanyResearchWorkspace,
        include_process: bool,
    ) -> tuple[CompanyResearchRunProcessEntry, ...]:
        projected = projection.process
        matched = []
        event_position = 0
        for entry in projected:
            while event_position < len(events) and (
                events[event_position].event_type != entry.code
            ):
                event_position += 1
            if event_position >= len(events):
                raise ValidationError(
                    "company research process projection is inconsistent"
                )
            event = events[event_position]
            matched.append((entry, event))
            event_position += 1
        retry = None
        if workspace.preparation.error is not None:
            retry = CompanyResearchRunRetry(
                retryable=workspace.preparation.error.retryable,
                next_attempt_at=(
                    _stored_utc(
                        workspace.preparation.error.next_attempt_at,
                        "retry timestamp",
                    )
                    if workspace.preparation.error.next_attempt_at is not None
                    else None
                ),
            )
        values = tuple(
            CompanyResearchRunProcessEntry(
                code=entry.code,
                message=entry.message,
                occurred_at=_stored_utc(event.created_at, "event timestamp"),
                retry=(retry if index == len(matched) - 1 else None),
            )
            for index, (entry, event) in enumerate(matched)
        )
        limit = _FULL_PROCESS_LIMIT if include_process else _RECENT_PROCESS_LIMIT
        return values[-limit:]

    def read(
        self, project_id: UUID, include_process: bool = False
    ) -> CompanyResearchRun:
        if type(project_id) is not UUID:
            raise ValidationError("project_id must be a UUID")
        if type(include_process) is not bool:
            raise ValidationError("include_process must be a boolean")

        # Authentication order is intentional: no lifecycle event is trusted until
        # the project/workspace ownership and frozen selection have been proved.
        authenticated = authenticated_company_research_workspace(
            self._session, project_id=project_id, now=self._now
        )
        workspace = authenticated.workspace
        events = self._company.events(workspace.preparation.id, fresh=True)
        preparation = self._company.preparation_for_project(project_id, fresh=True)
        if preparation is None or preparation.id != workspace.preparation.id:
            raise ValidationError("company research preparation is inconsistent")
        has_machine_memo = any(item.kind == "memo" for item in workspace.artifacts)
        try:
            projection = project_research_run(
                ResearchRunPreparation(
                    workspace.preparation.status,
                    workspace.preparation.progress,
                    (
                        workspace.preparation.error.code
                        if workspace.preparation.error is not None
                        else None
                    ),
                ),
                event_types=tuple(event.event_type for event in events),
                has_machine_memo=has_machine_memo,
                confirmation_complete=any(
                    event.event_type == "judgment_confirmed" for event in events
                ),
            )
        except ResearchRunProjectionError as exc:
            raise ValidationError("company research run projection is invalid") from exc
        critical_inputs = self._critical_inputs(
            workspace, has_machine_memo=has_machine_memo
        )
        company, securities = self._identities(
            workspace, frozen_revision=authenticated.frozen_revision
        )
        started_at = _stored_utc(preparation.created_at, "start timestamp")
        updated_at = max(
            _stored_utc(preparation.updated_at, "update timestamp"),
            *(_stored_utc(event.created_at, "event timestamp") for event in events),
        )
        return CompanyResearchRun(
            project_id=project_id,
            company=company,
            securities=securities,
            status=projection.status,
            progress=workspace.preparation.progress,
            started_at=started_at,
            updated_at=updated_at,
            stages=projection.stages,
            recent_process=self._process(
                projection=projection,
                events=events,
                workspace=workspace,
                include_process=include_process,
            ),
            workspace=workspace,
            critical_inputs=critical_inputs,
            selected_revision=workspace.selected_revision,
        )
