"""Redacted operational readiness contracts for administrators and Case readers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.v1.common import V1Model


RuntimeHealth = Literal["healthy", "degraded", "unavailable"]


class RuntimeCheckDTO(V1Model):
    status: RuntimeHealth


class MigrationStatusDTO(RuntimeCheckDTO):
    current: list[str]
    expected: list[str]


class ProviderConfigurationDTO(V1Model):
    service: Literal["api", "research-worker", "acquisition-worker", "scheduler"]
    status: Literal["configured", "misconfigured", "unconfirmed"]
    basis: Literal["local_process", "service_heartbeat"]
    observed_at: datetime | None
    missing: list[str]
    invalid: list[str]


class RuntimeServiceDTO(V1Model):
    name: Literal["research-worker", "acquisition-worker", "scheduler"]
    status: RuntimeHealth
    state: str | None
    last_seen_at: datetime | None


class RuntimeStatusDTO(V1Model):
    status: RuntimeHealth
    database: RuntimeCheckDTO
    migration: MigrationStatusDTO
    providers: list[ProviderConfigurationDTO]
    services: list[RuntimeServiceDTO]


class DurableCheckpointDTO(V1Model):
    workflow_state: str
    user_stage: str
    version: int
    system_action: str | None
    saved_at: datetime
    last_transition: str | None
    last_transition_at: datetime | None


class CaseRuntimeRecoveryDTO(V1Model):
    automatic: bool
    status: str
    message: str


class CaseRuntimeStatusDTO(V1Model):
    runtime_status: RuntimeHealth
    required_services: list[RuntimeServiceDTO]
    durable_checkpoint: DurableCheckpointDTO
    recovery: CaseRuntimeRecoveryDTO
    message: str
