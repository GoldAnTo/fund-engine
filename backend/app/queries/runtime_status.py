"""Read operational health beside, never in place of, durable Case state."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.research_orchestration import (
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.schemas.v1.runtime_status import (
    CaseRuntimeRecoveryDTO,
    CaseRuntimeStatusDTO,
    DurableCheckpointDTO,
    MigrationStatusDTO,
    ProviderConfigurationDTO,
    RuntimeCheckDTO,
    RuntimeServiceDTO,
    RuntimeStatusDTO,
)
from app.scripts.runtime_probe import expected_migration_heads
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.runtime_configuration import (
    PUBLIC_CONFIGURATION_ISSUES,
    provider_configuration_status,
)


_SERVICES = ("research-worker", "acquisition-worker", "scheduler")
_PUBLIC_RUNTIME_STATES = frozenset({"polling", "executing", "idle", "running"})
_ACTIVE_AUTOMATION_STATES = frozenset(
    {
        "planning_acquisition",
        "acquiring",
        "freezing_sources",
        "assessing_coverage",
        "synthesizing_evidence",
        "adjudicating_thesis",
        "generating_report",
        "monitoring",
        "retry_wait",
        "recovering",
    }
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _required_services(orchestration: ResearchOrchestration) -> tuple[str, ...]:
    state = orchestration.state
    if state in {
        "planning_acquisition",
        "acquiring",
        "freezing_sources",
        "assessing_coverage",
    }:
        return ("scheduler", "acquisition-worker")
    if state in {
        "synthesizing_evidence",
        "adjudicating_thesis",
        "generating_report",
    }:
        return ("scheduler", "research-worker")
    if state == "monitoring":
        return ("scheduler",)
    if state in {"retry_wait", "recovering"}:
        if orchestration.user_stage == "acquisition":
            return ("scheduler", "acquisition-worker")
        if orchestration.user_stage == "report_monitoring":
            return ("scheduler",)
        return ("scheduler", "research-worker")
    return ()


class RuntimeStatusQueries:
    def __init__(
        self,
        session: Session,
        *,
        environment: Mapping[str, str] | None = None,
        clock=None,
    ) -> None:
        self._session = session
        self._environment = environment if environment is not None else os.environ
        self._clock = clock or (lambda: datetime.now(UTC))

    def _service(self, name: str) -> RuntimeServiceDTO:
        raw = WorkerHeartbeatService(self._session).status(
            service=name,
            now=self._clock(),
        )
        raw_status = raw["status"]
        status = (
            "unavailable"
            if raw.get("configuration_status") == "misconfigured"
            else "degraded"
            if raw_status == "available"
            and raw.get("configuration_status") != "configured"
            else "healthy"
            if raw_status == "available"
            else "degraded"
            if raw_status == "stale"
            else "unavailable"
        )
        return RuntimeServiceDTO(
            name=name,
            status=status,
            state=(
                raw["state"]
                if raw["state"] in _PUBLIC_RUNTIME_STATES
                else "unknown"
                if raw["state"] is not None
                else None
            ),
            last_seen_at=raw["last_seen_at"],
        )

    def _migration(self) -> MigrationStatusDTO:
        expected = sorted(expected_migration_heads())
        try:
            current = sorted(
                str(row[0])
                for row in self._session.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            )
        except SQLAlchemyError:
            # The request already proved the database connection works.  A
            # missing/unreadable revision table is a migration-readiness fact.
            self._session.rollback()
            return MigrationStatusDTO(
                status="unavailable",
                current=[],
                expected=expected,
            )
        return MigrationStatusDTO(
            status="healthy" if current == expected else "degraded",
            current=current,
            expected=expected,
        )

    def admin(self) -> RuntimeStatusDTO:
        self._session.execute(text("SELECT 1"))
        migration = self._migration()
        providers = []
        api_configuration = provider_configuration_status("api", self._environment)
        providers.append(
            ProviderConfigurationDTO(
                service="api",
                status=(
                    "configured"
                    if api_configuration["status"] == "healthy"
                    else "misconfigured"
                ),
                basis="local_process",
                observed_at=self._clock(),
                missing=list(api_configuration.get("missing", [])),
                invalid=list(api_configuration.get("invalid", [])),
            )
        )
        for service in _SERVICES:
            raw = WorkerHeartbeatService(self._session).status(
                service=service,
                now=self._clock(),
            )
            configuration_status = raw.get("configuration_status")
            status = (
                configuration_status
                if configuration_status in {"configured", "misconfigured"}
                else "unconfirmed"
            )
            providers.append(
                ProviderConfigurationDTO(
                    service=service,
                    status=status,
                    basis="service_heartbeat",
                    observed_at=raw.get("last_seen_at"),
                    missing=(
                        [
                            item
                            for item in raw.get("configuration_issues", {}).get("missing", [])
                            if item in PUBLIC_CONFIGURATION_ISSUES
                        ]
                        if status != "configured"
                        else []
                    ),
                    invalid=(
                        [
                            item
                            for item in raw.get("configuration_issues", {}).get("invalid", [])
                            if item in PUBLIC_CONFIGURATION_ISSUES
                        ]
                        if status != "configured"
                        else []
                    ),
                )
            )
        services = [self._service(name) for name in _SERVICES]
        status = "healthy"
        if (
            migration.status != "healthy"
            or any(item.status != "configured" for item in providers)
            or any(item.status != "healthy" for item in services)
        ):
            status = "degraded"
        return RuntimeStatusDTO(
            status=status,
            database=RuntimeCheckDTO(status="healthy"),
            migration=migration,
            providers=providers,
            services=services,
        )

    def case(self, case_id: uuid.UUID, *, tenant_id: str) -> CaseRuntimeStatusDTO:
        orchestration = self._session.scalar(
            select(ResearchOrchestration).where(
                ResearchOrchestration.research_case_id == case_id,
                ResearchOrchestration.tenant_id == tenant_id,
            )
        )
        if orchestration is None:
            raise NotFoundError("durable research workflow not found")
        latest_event = self._session.scalar(
            select(ResearchOrchestrationEvent)
            .where(ResearchOrchestrationEvent.orchestration_id == orchestration.id)
            .order_by(ResearchOrchestrationEvent.sequence.desc())
            .limit(1)
        )
        required = [self._service(name) for name in _required_services(orchestration)]
        runtime_status = "healthy"
        if any(item.status == "unavailable" for item in required):
            runtime_status = "unavailable"
        elif any(item.status == "degraded" for item in required):
            runtime_status = "degraded"
        automatic = orchestration.state in _ACTIVE_AUTOMATION_STATES
        if orchestration.recovery_status == "failed" or orchestration.state == "failed":
            automatic = False
            recovery_message = "自动恢复已失败，需要查看失败记录并人工处理。"
        elif orchestration.recovery_status == "recovering" or orchestration.state == "recovering":
            recovery_message = "系统已从耐久检查点开始自动恢复。"
        elif automatic:
            recovery_message = "服务恢复后会从耐久检查点继续，不会从头重做。"
        else:
            recovery_message = "当前阶段不需要自动恢复；按页面中的下一步操作继续。"
        return CaseRuntimeStatusDTO(
            runtime_status=runtime_status,
            required_services=required,
            durable_checkpoint=DurableCheckpointDTO(
                workflow_state=orchestration.state,
                user_stage=orchestration.user_stage,
                version=orchestration.version,
                system_action=orchestration.current_system_action,
                saved_at=_utc(orchestration.updated_at),
                last_transition=(latest_event.transition if latest_event else None),
                last_transition_at=(
                    _utc(latest_event.created_at) if latest_event else None
                ),
            ),
            recovery=CaseRuntimeRecoveryDTO(
                automatic=automatic,
                status=orchestration.recovery_status or "not_started",
                message=recovery_message,
            ),
            message=(
                "运行健康与 Case 进度分别读取；不会用运行健康推断 Case 阶段。"
            ),
        )
