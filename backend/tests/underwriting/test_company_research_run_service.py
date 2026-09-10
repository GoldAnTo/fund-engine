from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.ledger import ValidationError
from app.underwriting.persistence.company_research_models import CompanyResearchEvent
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchRepository,
)
from app.underwriting.services import company_research_run as run_module
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_run import (
    CompanyResearchRunService,
    authenticated_company_research_workspace,
)
from tests.underwriting.test_company_research_persistence import _tamper_row
from tests.underwriting.test_company_research_workbench import _model_workspace
from tests.underwriting.test_company_research_worker import NOW, _mainline_initialized


def _mainline_machine_draft(session, *, key: str):
    initialized = _mainline_initialized(session, idempotency_key=key)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    source_claim = worker.claim_next()
    assert source_claim is not None
    assert worker.run_claim(source_claim) == "building_model"
    model_claim = worker.claim_next()
    assert model_claim is not None
    assert worker.run_claim(model_claim) == "awaiting_judgment_review"
    return initialized


def test_company_research_run_service_composes_authenticated_mainline_heads(
    session,
) -> None:
    initialized = _mainline_machine_draft(session, key="company-run-service-mainline")

    recent = CompanyResearchRunService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    full = CompanyResearchRunService(session, now=lambda: NOW).read(
        initialized.project.id, include_process=True
    )

    assert recent.workspace.project_id == initialized.project.id
    assert recent.company.object_id == recent.workspace.company.id
    assert {item.object_id for item in recent.securities} == set(
        initialized.project.target_security_ids
    )
    assert recent.critical_inputs is not None
    assert recent.critical_inputs.value.inputs
    assert len(recent.recent_process) == 3
    assert 3 < len(full.recent_process) <= 100
    assert recent.recent_process == full.recent_process[-3:]
    assert all(item.occurred_at.tzinfo is not None for item in full.recent_process)
    assert recent.selected_revision is None


def test_company_research_run_service_authenticates_workspace_before_event_read(
    session, monkeypatch
) -> None:
    calls: list[str] = []

    def reject_workspace(*args, **kwargs):
        calls.append("workspace")
        raise ValidationError("workspace authentication failed")

    def unexpected_events(*args, **kwargs):
        calls.append("events")
        return ()

    monkeypatch.setattr(
        run_module, "authenticated_company_research_workspace", reject_workspace
    )
    monkeypatch.setattr(CompanyResearchRepository, "events", unexpected_events)

    with pytest.raises(ValidationError, match="workspace authentication failed"):
        CompanyResearchRunService(session, now=lambda: NOW).read(uuid4())

    assert calls == ["workspace"]


def test_company_research_run_service_rejects_the_whole_malformed_event_chain(
    session,
) -> None:
    initialized = _mainline_machine_draft(
        session, key="company-run-service-event-integrity"
    )
    events = CompanyResearchRepository(session).events(initialized.preparation.id)
    _tamper_row(
        session,
        CompanyResearchEvent,
        events[-1].id,
        content_hash="f" * 64,
    )

    with pytest.raises(CompanyResearchIntegrityError, match="content hash"):
        CompanyResearchRunService(session, now=lambda: NOW).read(initialized.project.id)


def test_company_research_run_service_requires_mainline_critical_input_head(
    session, monkeypatch
) -> None:
    initialized = _mainline_machine_draft(
        session, key="company-run-service-critical-head"
    )
    original = CompanyResearchRepository.current_artifact

    def without_critical_inputs(repository, project_id, kind, *, lock=False):
        if kind == "critical_inputs":
            return None
        return original(repository, project_id, kind, lock=lock)

    monkeypatch.setattr(
        CompanyResearchRepository, "current_artifact", without_critical_inputs
    )

    with pytest.raises(ValidationError, match="requires critical inputs"):
        CompanyResearchRunService(session, now=lambda: NOW).read(initialized.project.id)


def test_company_research_run_service_keeps_legacy_workspace_readable(session) -> None:
    initialized, _workbench, repository = _model_workspace(session)
    repository.append_event(
        preparation_id=initialized.preparation.id,
        event_type="model_stage_claimed",
        payload={"stage": "model_bundle", "attempt": 1},
        created_at=NOW,
    )

    result = CompanyResearchRunService(session, now=lambda: NOW).read(
        initialized.project.id
    )

    assert result.workspace.preparation.strategy_version == (
        "company-research-default.v1"
    )
    assert result.critical_inputs is None
    assert result.status.value == "needs_input"


def test_company_research_run_service_rejects_foreign_identity_projection(
    session, monkeypatch
) -> None:
    initialized = _mainline_machine_draft(
        session, key="company-run-service-foreign-identity"
    )
    original = run_module.build_alphabet_company_research_preview_at_cutoff

    def foreign_preview(*args, **kwargs):
        preview = original(*args, **kwargs)
        return SimpleNamespace(
            company=SimpleNamespace(
                object_id=uuid4(),
                external_key=preview.company.external_key,
                canonical_name=preview.company.canonical_name,
            ),
            securities=preview.securities,
        )

    monkeypatch.setattr(
        run_module,
        "build_alphabet_company_research_preview_at_cutoff",
        foreign_preview,
    )

    with pytest.raises(ValidationError, match="identity is inconsistent"):
        CompanyResearchRunService(session, now=lambda: NOW).read(initialized.project.id)


def test_company_research_run_service_rejects_inconsistent_selected_revision(
    session, monkeypatch
) -> None:
    initialized, workbench, _repository = _model_workspace(session)
    workspace = workbench.workspace(project_id=initialized.project.id)
    revision_id = uuid4()
    selected = replace(workspace, selected_revision=revision_id)
    monkeypatch.setattr(
        run_module.CompanyResearchWorkbench,
        "workspace",
        lambda *args, **kwargs: selected,
    )
    monkeypatch.setattr(
        run_module.CompanyResearchPublicationService,
        "revision",
        lambda *args, **kwargs: SimpleNamespace(
            id=revision_id,
            artifacts=workspace.artifacts,
            company=SimpleNamespace(
                object_id=workspace.company.id,
                external_key="FOREIGN:COMPANY",
                canonical_name=workspace.company.canonical_name,
            ),
        ),
    )

    with pytest.raises(ValidationError, match="selected frozen revision"):
        authenticated_company_research_workspace(
            session, project_id=initialized.project.id, now=lambda: NOW
        )


def test_company_research_run_service_requires_selected_revision_to_bind_critical_head(
    session, monkeypatch
) -> None:
    initialized = _mainline_machine_draft(
        session, key="company-run-service-selected-critical"
    )
    workspace = run_module.CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
        project_id=initialized.project.id
    )
    revision_id = uuid4()
    selected = replace(workspace, selected_revision=revision_id)
    monkeypatch.setattr(
        run_module.CompanyResearchWorkbench,
        "workspace",
        lambda *args, **kwargs: selected,
    )
    monkeypatch.setattr(
        run_module.CompanyResearchPublicationService,
        "revision",
        lambda *args, **kwargs: SimpleNamespace(
            id=revision_id,
            artifacts=workspace.artifacts,
            company=SimpleNamespace(
                object_id=workspace.company.id,
                external_key=workspace.company.external_key,
                canonical_name=workspace.company.canonical_name,
            ),
        ),
    )

    with pytest.raises(ValidationError, match="selected frozen revision"):
        authenticated_company_research_workspace(
            session, project_id=initialized.project.id, now=lambda: NOW
        )


def test_company_research_run_service_rejects_selected_revision_security_mismatch(
    session, monkeypatch
) -> None:
    initialized = _mainline_machine_draft(
        session, key="company-run-service-selected-security"
    )
    baseline = CompanyResearchRunService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    workspace = baseline.workspace
    critical = CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert critical is not None
    revision_id = uuid4()
    selected = replace(workspace, selected_revision=revision_id)
    frozen_artifacts = (
        *workspace.artifacts,
        SimpleNamespace(
            kind=critical.kind,
            id=critical.id,
            version=critical.version,
            input_hash=critical.input_hash,
            content_hash=critical.content_hash,
        ),
    )
    frozen_securities = (
        replace(baseline.securities[0], symbol="MISMATCH"),
        *baseline.securities[1:],
    )
    frozen = SimpleNamespace(
        id=revision_id,
        artifacts=frozen_artifacts,
        company=baseline.company,
        securities=frozen_securities,
    )
    revision_calls = 0

    def frozen_revision(*args, **kwargs):
        nonlocal revision_calls
        revision_calls += 1
        return frozen

    monkeypatch.setattr(
        run_module.CompanyResearchWorkbench,
        "workspace",
        lambda *args, **kwargs: selected,
    )
    monkeypatch.setattr(
        run_module.CompanyResearchPublicationService,
        "revision",
        frozen_revision,
    )

    with pytest.raises(ValidationError, match="selected frozen revision"):
        CompanyResearchRunService(session, now=lambda: NOW).read(initialized.project.id)

    assert revision_calls == 1


def test_company_research_run_service_bounds_process_and_attaches_typed_retry() -> None:
    entries = tuple(
        SimpleNamespace(code=f"event-{index}", message=f"Safe event {index}")
        for index in range(105)
    )
    events = tuple(
        SimpleNamespace(event_type=item.code, created_at=NOW) for item in entries
    )
    workspace = SimpleNamespace(
        preparation=SimpleNamespace(
            error=SimpleNamespace(retryable=True, next_attempt_at=NOW)
        )
    )

    recent = CompanyResearchRunService._process(
        projection=SimpleNamespace(process=entries),
        events=events,
        workspace=workspace,
        include_process=False,
    )
    full = CompanyResearchRunService._process(
        projection=SimpleNamespace(process=entries),
        events=events,
        workspace=workspace,
        include_process=True,
    )

    assert len(recent) == 3
    assert len(full) == 100
    assert recent == full[-3:]
    assert recent[-1].retry is not None
    assert recent[-1].retry.retryable is True
    assert recent[-1].retry.next_attempt_at == NOW
    assert all(item.retry is None for item in recent[:-1])
