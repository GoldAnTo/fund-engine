"""Route inventory and API proofs for literal Case-path authorization."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_type_hints
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.dependencies import (
    CASE_ROUTE_PERMISSIONS,
    INDIRECT_CASE_ROUTE_PERMISSIONS,
    RequireCaseRoute,
    UNSCOPED_CASE_ROUTE_OPERATIONS,
    require_case_route_permission,
)
from app.api.v1.tenant_context import require_research_actor
from app.main import app
from app.models.identity import CaseAccessGrant, ResearchUser
from app.models.ledger import (
    AIAssessment,
    AtomicClaimCandidate,
    CaseDocumentVersion,
    CaseTenantAdmission,
    Company,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
    ThemeRole,
)
from app.models.proposals import Proposal
from app.repositories.research import ResearchRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.security.principal import ResearchPrincipal
from app.services.case_authorization import CaseAuthorizationService
from app.services.event_research import EventResearchService
from app.services.themes import ThemeService


EXPECTED_CASE_OPERATION_NAMES = frozenset(CASE_ROUTE_PERMISSIONS)


def _case_operations() -> list[tuple[str, str, str]]:
    operations: list[tuple[str, str, str]] = []
    schema = app.openapi()
    for path, path_item in schema["paths"].items():
        if "{case_id}" not in path:
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is not None:
                operations.append((method.upper(), path, operation["operationId"]))
    return operations


def _declared_name(operation_id: str) -> str:
    matches = [
        name
        for name in EXPECTED_CASE_OPERATION_NAMES
        if operation_id.startswith(f"{name}_")
    ]
    assert matches, f"unclassified Case operation: {operation_id}"
    # FastAPI prefixes operation IDs with the route name.  Some names are a
    # strict prefix of another (workflow vs workflow_events), so the longest
    # matching declaration is the exact route name.
    return max(matches, key=len)


def test_every_literal_case_route_has_one_explicit_permission() -> None:
    operations = _case_operations()
    declared = {_declared_name(operation_id) for _, _, operation_id in operations}

    assert len(operations) == 67
    assert declared == EXPECTED_CASE_OPERATION_NAMES
    assert Counter(CASE_ROUTE_PERMISSIONS.values()) == {
        "view": 29,
        "edit": 19,
        "review": 14,
        "admin": 5,
    }
    assert any(path.startswith("/api/research-cases/") for _, path, _ in operations)


def test_every_literal_case_route_requires_authentication_before_handler_input() -> None:
    client = TestClient(app)
    case_id = "00000000-0000-0000-0000-000000000001"
    other_id = "00000000-0000-0000-0000-000000000002"

    for method, path, operation_id in _case_operations():
        url = path.replace("{case_id}", case_id)
        for parameter in (
            "version_id",
            "factor_id",
            "edge_id",
            "run_id",
            "thesis_id",
            "user_id",
        ):
            url = url.replace(f"{{{parameter}}}", other_id)
        url = url.replace("{target_status}", "active")
        response = client.request(method, url, json={})
        assert response.status_code == 401, (
            method,
            path,
            operation_id,
            response.status_code,
            response.text,
        )


def _operation_for_name(name: str) -> tuple[str, str, str]:
    matches: list[tuple[str, str, str]] = []
    for path, path_item in app.openapi()["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            operation_id = operation["operationId"]
            declarations = [
                declared
                for declared in INDIRECT_CASE_ROUTE_PERMISSIONS
                if operation_id.startswith(f"{declared}_")
            ]
            if declarations and max(declarations, key=len) == name:
                matches.append((method.upper(), path, operation["operationId"]))
    assert len(matches) == 1, (name, matches)
    return matches[0]


def test_indirect_case_route_inventory_is_present_and_authenticated() -> None:
    client = TestClient(app)
    value = "00000000-0000-0000-0000-000000000001"

    assert Counter(INDIRECT_CASE_ROUTE_PERMISSIONS.values()) == {
        "view": 28,
        "edit": 15,
        "review": 10,
        "admin": 1,
    }
    for name in INDIRECT_CASE_ROUTE_PERMISSIONS:
        method, path, operation_id = _operation_for_name(name)
        url = path
        for parameter in (
            "task_id",
            "job_id",
            "run_id",
            "candidate_id",
            "target_id",
            "binding_id",
            "relation_id",
            "thesis_id",
            "document_version_id",
            "company_id",
            "proposal_id",
            "link_id",
            "assessment_id",
        ):
            url = url.replace(f"{{{parameter}}}", value)
        response = client.request(method, url, json={})
        assert response.status_code == 401, (
            method,
            path,
            operation_id,
            response.status_code,
            response.text,
        )


def test_every_case_id_query_parameter_is_in_the_indirect_inventory() -> None:
    missing: list[tuple[str, str, str]] = []
    for path, path_item in app.openapi()["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            has_case_query = any(
                parameter.get("name") == "case_id"
                and parameter.get("in") == "query"
                for parameter in operation.get("parameters", [])
            )
            if not has_case_query:
                continue
            operation_id = operation["operationId"]
            if not any(
                operation_id.startswith(f"{name}_")
                for name in INDIRECT_CASE_ROUTE_PERMISSIONS
            ):
                missing.append((method.upper(), path, operation_id))
    assert missing == []


def _effective_api_routes():
    for route in app.routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if effective_route_contexts is None:
            if getattr(route, "path", "").startswith("/api/"):
                yield route
            continue
        for context in effective_route_contexts():
            if context.path.startswith("/api/"):
                yield context


def test_case_route_inventory_is_exhaustive_and_runtime_enforced() -> None:
    case_policies = set(CASE_ROUTE_PERMISSIONS) | set(
        INDIRECT_CASE_ROUTE_PERMISSIONS
    )
    declared_operations = case_policies | set(UNSCOPED_CASE_ROUTE_OPERATIONS)
    routes = list(_effective_api_routes())

    assert {route.name for route in routes} == declared_operations
    for route in routes:
        dependency_calls = {
            dependency.dependency for dependency in route.dependencies
        }
        if route.name in case_policies:
            assert require_case_route_permission in dependency_calls, (
                route.name,
                route.path,
            )
        if route.name in INDIRECT_CASE_ROUTE_PERMISSIONS:
            assert RequireCaseRoute in get_type_hints(
                route.endpoint,
                include_extras=True,
            ).values(), (route.name, route.path)


def _principal(cmd_session, *, tenant_id: str, label: str) -> ResearchPrincipal:
    now = datetime.now(UTC)
    user = ResearchUser(
        issuer="https://test-identity.invalid/realms/research",
        subject=f"{tenant_id}:{label}:{uuid.uuid4()}",
        tenant_id=tenant_id,
        display_name=label,
        normalized_email=None,
        active=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(user)
    cmd_session.flush()
    return ResearchPrincipal(
        user_id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        roles=frozenset(),
        expires_at=now + timedelta(hours=1),
    )


def _as_principal(principal: ResearchPrincipal):
    previous = app.dependency_overrides.get(require_research_actor)
    app.dependency_overrides[require_research_actor] = lambda: principal

    def restore() -> None:
        if previous is None:
            app.dependency_overrides.pop(require_research_actor, None)
        else:
            app.dependency_overrides[require_research_actor] = previous

    return restore


def _request_as(cmd_client, principal, method: str, url: str, *, body=None):
    restore = _as_principal(principal)
    try:
        return cmd_client.request(method, url, json=body)
    finally:
        restore()


def test_prototype_case_creation_admits_case_and_grants_server_principal_owner(
    cmd_client,
    cmd_session,
) -> None:
    creator = _principal(cmd_session, tenant_id="team-a", label="creator")

    response = _request_as(
        cmd_client,
        creator,
        "POST",
        "/api/v1/research-cases",
        body={
            "title": "Server-owned prototype Case",
            "industry_topic": "event research",
            "initial_theses": [{"statement": "Demand changes earnings"}],
        },
    )

    assert response.status_code == 201, response.text
    case_id = uuid.UUID(response.json()["case_id"])
    research_case = cmd_session.get(ResearchCase, case_id)
    admission = cmd_session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    grant = cmd_session.scalar(
        select(CaseAccessGrant).where(
            CaseAccessGrant.research_case_id == case_id,
            CaseAccessGrant.user_id == creator.user_id,
        )
    )
    assert research_case is not None and research_case.created_by == creator.actor
    assert admission is not None and admission.tenant_id == creator.tenant_id
    assert admission.admitted_by == creator.actor
    assert grant is not None and grant.role == "owner"


def test_indirect_case_resources_apply_view_and_edit_grants(
    cmd_client,
    cmd_session,
) -> None:
    owner = _principal(cmd_session, tenant_id="team-a", label="indirect-owner")
    viewer = _principal(cmd_session, tenant_id="team-a", label="indirect-viewer")
    editor = _principal(cmd_session, tenant_id="team-a", label="indirect-editor")
    reviewer = _principal(
        cmd_session,
        tenant_id="team-a",
        label="indirect-reviewer",
    )
    ungranted = _principal(
        cmd_session,
        tenant_id="team-a",
        label="indirect-ungranted",
    )
    created = EventResearchService(cmd_session).create(
        CreateEventResearchRequest(
            raw_input="Frozen indirect route source.",
            event_title="Indirect authorization needle",
            research_question="Can resources resolve their Case safely?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=owner,
    )
    case_id = uuid.UUID(created.case_id)
    authorization = CaseAuthorizationService(cmd_session)
    authorization.grant(case_id, owner, viewer.user_id, "viewer")
    authorization.grant(case_id, owner, editor.user_id, "editor")
    authorization.grant(case_id, owner, reviewer.user_id, "reviewer")
    thesis_id = cmd_session.scalar(
        select(Thesis.id)
        .where(Thesis.research_case_id == case_id)
        .order_by(Thesis.created_at, Thesis.id)
    )
    document_id = cmd_session.scalar(
        select(CaseTenantAdmission.initial_document_version_id).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    assert thesis_id is not None and document_id is not None
    cmd_session.commit()

    assert _request_as(
        cmd_client,
        viewer,
        "GET",
        f"/api/v1/documents/{document_id}",
    ).status_code == 200
    assert _request_as(
        cmd_client,
        ungranted,
        "GET",
        f"/api/v1/documents/{document_id}",
    ).status_code == 404
    assert _request_as(
        cmd_client,
        viewer,
        "GET",
        f"/api/v1/overview?case_id={case_id}",
    ).status_code == 200
    assert _request_as(
        cmd_client,
        ungranted,
        "GET",
        f"/api/v1/overview?case_id={case_id}",
    ).status_code == 404
    assert _request_as(
        cmd_client,
        ungranted,
        "GET",
        f"/api/v1/research-cases/{case_id}/fund-exposure",
    ).status_code == 404

    proposals_url = f"/api/v1/review-proposals?case_id={case_id}"
    assert _request_as(
        cmd_client,
        reviewer,
        "GET",
        proposals_url,
    ).status_code == 200
    assert _request_as(
        cmd_client,
        editor,
        "GET",
        proposals_url,
    ).status_code == 403
    assert _request_as(
        cmd_client,
        ungranted,
        "GET",
        proposals_url,
    ).status_code == 404

    viewer_search = _request_as(
        cmd_client,
        viewer,
        "GET",
        "/api/v1/search?q=Indirect%20authorization%20needle&types=case",
    )
    hidden_search = _request_as(
        cmd_client,
        ungranted,
        "GET",
        "/api/v1/search?q=Indirect%20authorization%20needle&types=case",
    )
    assert viewer_search.status_code == 200
    assert any(
        hit["case_id"] == str(case_id)
        for group in viewer_search.json()["groups"]
        for hit in group["hits"]
    )
    assert all(
        hit["case_id"] != str(case_id)
        for group in hidden_search.json()["groups"]
        for hit in group["hits"]
    )

    step_url = f"/api/v1/theses/{thesis_id}/causal-steps"
    payload = {"description": "Demand changes utilization", "sequence": 1}
    assert _request_as(
        cmd_client,
        viewer,
        "POST",
        step_url,
        body=payload,
    ).status_code == 403
    assert _request_as(
        cmd_client,
        ungranted,
        "POST",
        step_url,
        body=payload,
    ).status_code == 404
    assert _request_as(
        cmd_client,
        editor,
        "POST",
        step_url,
        body=payload,
    ).status_code == 201


def test_shared_document_extraction_requires_edit_on_every_attached_case(
    cmd_client,
    cmd_session,
) -> None:
    owner_a = _principal(cmd_session, tenant_id="team-a", label="shared-owner-a")
    owner_b = _principal(cmd_session, tenant_id="team-a", label="shared-owner-b")
    editor_a = _principal(cmd_session, tenant_id="team-a", label="shared-editor-a")

    def create(principal, label: str) -> uuid.UUID:
        result = EventResearchService(cmd_session).create(
            CreateEventResearchRequest(
                raw_input=f"Frozen shared document source {label}.",
                event_title=f"Shared extraction {label}",
                research_question="Can extraction mutate shared material?",
                candidate_factors=["Demand", "Capacity", "Margin"],
            ),
            principal=principal,
        )
        return uuid.UUID(result.case_id)

    case_a = create(owner_a, "A")
    case_b = create(owner_b, "B")
    document_a = cmd_session.scalar(
        select(CaseTenantAdmission.initial_document_version_id).where(
            CaseTenantAdmission.research_case_id == case_a
        )
    )
    assert document_a is not None
    CaseAuthorizationService(cmd_session).grant(
        case_a,
        owner_a,
        editor_a.user_id,
        "editor",
    )
    cmd_session.add(
        CaseDocumentVersion(
            research_case_id=case_b,
            document_version_id=document_a,
            linked_at=datetime.now(UTC),
        )
    )
    cmd_session.commit()

    response = _request_as(
        cmd_client,
        editor_a,
        "POST",
        f"/api/v1/documents/{document_a}/extract",
    )
    assert response.status_code == 404


def test_provider_output_rechecks_edit_permission_after_revocation(
    cmd_client,
    cmd_session,
    monkeypatch,
) -> None:
    from app.ai.client import LLMClient

    owner = _principal(cmd_session, tenant_id="team-a", label="race-owner")
    editor = _principal(cmd_session, tenant_id="team-a", label="race-editor")
    created = EventResearchService(cmd_session).create(
        CreateEventResearchRequest(
            raw_input="Frozen provider race source.",
            event_title="Provider authorization race",
            research_question="Does revocation stop provider output?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=owner,
    )
    case_id = uuid.UUID(created.case_id)
    authorization = CaseAuthorizationService(cmd_session)
    authorization.grant(case_id, owner, editor.user_id, "editor")
    thesis_id = cmd_session.scalar(
        select(Thesis.id).where(Thesis.research_case_id == case_id)
    )
    document_id = cmd_session.scalar(
        select(CaseTenantAdmission.initial_document_version_id).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    assert thesis_id is not None and document_id is not None
    cmd_session.commit()

    monkeypatch.setattr(
        "app.api.v1.commands.engine.LLMClient.from_env",
        lambda: LLMClient(model_version="authorization-race", mock=True),
    )

    def revoke_during_propose(
        _self,
        _thesis_id,
        session,
        *,
        before_persist=None,
        **_kwargs,
    ):
        CaseAuthorizationService(session).revoke(
            case_id,
            owner,
            editor.user_id,
            reason="revoke while provider is running",
        )
        session.commit()
        assert before_persist is not None
        before_persist()
        return []

    monkeypatch.setattr(
        "app.api.v1.commands.engine.EvidenceProposer.propose",
        revoke_during_propose,
    )
    propose = _request_as(
        cmd_client,
        editor,
        "POST",
        f"/api/v1/theses/{thesis_id}/propose",
    )
    assert propose.status_code == 404
    assert list(
        cmd_session.scalars(
            select(Proposal).where(Proposal.research_case_id == case_id)
        )
    ) == []

    authorization.grant(case_id, owner, editor.user_id, "editor")
    cmd_session.commit()

    def revoke_during_extract(
        _self,
        _document_version_id,
        session,
        *,
        before_persist=None,
        **_kwargs,
    ):
        CaseAuthorizationService(session).revoke(
            case_id,
            owner,
            editor.user_id,
            reason="revoke while extraction provider is running",
        )
        session.commit()
        assert before_persist is not None
        before_persist()
        return []

    monkeypatch.setattr(
        "app.api.v1.commands.engine.StatementExtractor.extract",
        revoke_during_extract,
    )
    extract = _request_as(
        cmd_client,
        editor,
        "POST",
        f"/api/v1/documents/{document_id}/extract",
    )
    assert extract.status_code == 404
    assert list(cmd_session.scalars(select(AtomicClaimCandidate))) == []

    authorization.grant(case_id, owner, editor.user_id, "editor")
    cmd_session.commit()

    def revoke_during_assessment(
        _self,
        _thesis_id,
        _cutoff,
        session,
        *,
        before_persist=None,
        **_kwargs,
    ):
        CaseAuthorizationService(session).revoke(
            case_id,
            owner,
            editor.user_id,
            reason="revoke while assessment provider is running",
        )
        session.commit()
        assert before_persist is not None
        before_persist()
        raise AssertionError("revoked assessment output must not continue")

    monkeypatch.setattr(
        "app.api.v1.commands.engine.AssessmentGenerator.generate",
        revoke_during_assessment,
    )
    rerun = _request_as(
        cmd_client,
        editor,
        "POST",
        f"/api/v1/theses/{thesis_id}/rerun",
    )
    assert rerun.status_code == 404
    assert list(cmd_session.scalars(select(EvidenceSnapshot))) == []
    assert list(cmd_session.scalars(select(AIAssessment))) == []


def test_case_api_distinguishes_invisible_from_insufficient_permissions(
    cmd_client,
    cmd_session,
) -> None:
    owner = _principal(cmd_session, tenant_id="team-a", label="owner")
    created = EventResearchService(cmd_session).create(
        CreateEventResearchRequest(
            raw_input="A material operating update.",
            event_title="Authorization route proof",
            research_question="Does the update alter expectations?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=owner,
    )
    case_id = uuid.UUID(created.case_id)
    service = CaseAuthorizationService(cmd_session)
    granted: dict[str, ResearchPrincipal] = {}
    for role in ("editor", "reviewer", "viewer"):
        principal = _principal(cmd_session, tenant_id="team-a", label=role)
        service.grant(case_id, owner, principal.user_id, role)
        granted[role] = principal
    ungranted = _principal(cmd_session, tenant_id="team-a", label="ungranted")
    service_principal = replace(
        _principal(cmd_session, tenant_id="team-a", label="service"),
        roles=frozenset({"service_principal"}),
    )
    tenant_admin = replace(
        _principal(cmd_session, tenant_id="team-a", label="tenant-admin"),
        roles=frozenset({"tenant_administrator"}),
    )
    foreign_admin = replace(
        _principal(cmd_session, tenant_id="team-b", label="foreign-admin"),
        roles=frozenset({"tenant_administrator"}),
    )
    cmd_session.commit()

    scope_history = f"/api/v1/event-research/{case_id}/scope-history"
    scope_write = f"/api/v1/event-research/{case_id}/scope"
    publish = f"/api/v1/event-research/{case_id}/conclusion/publish"

    expectations = [
        (owner, "GET", scope_history, None, 200),
        (granted["viewer"], "GET", scope_history, None, 200),
        (granted["viewer"], "PUT", scope_write, {}, 403),
        (granted["editor"], "PUT", scope_write, {}, 422),
        (granted["editor"], "POST", publish, {}, 403),
        (granted["reviewer"], "POST", publish, {}, 422),
        (ungranted, "GET", scope_history, None, 404),
        (service_principal, "GET", scope_history, None, 404),
        (tenant_admin, "GET", scope_history, None, 404),
        (foreign_admin, "GET", scope_history, None, 404),
    ]
    for principal, method, url, body, expected in expectations:
        restore = _as_principal(principal)
        try:
            response = cmd_client.request(method, url, json=body)
        finally:
            restore()
        assert response.status_code == expected, (
            principal.display_name,
            method,
            url,
            response.status_code,
            response.text,
        )


def test_theme_role_cannot_bridge_a_private_statement_into_another_case(
    cmd_client,
    cmd_session,
) -> None:
    victim = _principal(cmd_session, tenant_id="victim-team", label="victim")
    attacker = _principal(cmd_session, tenant_id="attacker-team", label="attacker")
    victim_case = EventResearchService(cmd_session).create(
        CreateEventResearchRequest(
            raw_input="Private victim source.",
            event_title="Victim private Case",
            research_question="Must this source stay private?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=victim,
    )
    attacker_case = EventResearchService(cmd_session).create(
        CreateEventResearchRequest(
            raw_input="Attacker-owned source.",
            event_title="Attacker Case",
            research_question="Can it reference only its own sources?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=attacker,
    )
    victim_case_id = uuid.UUID(victim_case.case_id)
    attacker_case_id = uuid.UUID(attacker_case.case_id)
    victim_document_id = cmd_session.scalar(
        select(CaseTenantAdmission.initial_document_version_id).where(
            CaseTenantAdmission.research_case_id == victim_case_id
        )
    )
    assert victim_document_id is not None
    span = SourceSpan(
        document_version_id=victim_document_id,
        locator={"page": 1},
        verbatim_text="VICTIM PRIVATE STATEMENT",
    )
    company = Company(
        code="PRIVATE-COMPANY",
        name="Private Company",
        type="listed",
        created_at=datetime.now(UTC),
    )
    cmd_session.add_all([span, company])
    cmd_session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="VICTIM PRIVATE STATEMENT",
        created_at=datetime.now(UTC),
    )
    cmd_session.add(statement)
    cmd_session.flush()
    cmd_session.commit()

    cross_case = _request_as(
        cmd_client,
        attacker,
        "POST",
        f"/api/v1/companies/{company.id}/theme-roles",
        body={
            "role": "malicious backlink",
            "research_case_id": str(attacker_case_id),
            "source_statement_id": str(statement.id),
        },
    )
    assert cross_case.status_code == 404

    cmd_session.add(
        ThemeRole(
            company_id=company.id,
            research_case_id=attacker_case_id,
            role="legacy polluted backlink",
            scope={},
            source_statement_id=statement.id,
            created_at=datetime.now(UTC),
        )
    )
    cmd_session.commit()
    dossier = _request_as(
        cmd_client,
        attacker,
        "GET",
        f"/api/v1/companies/{company.id}",
    )
    assert dossier.status_code == 200
    polluted = next(
        item
        for item in dossier.json()["theme_roles"]
        if item["role"] == "legacy polluted backlink"
    )
    assert polluted["statement_id"] is None
    assert polluted["statement_text"] is None
    assert polluted["span_id"] is None
    assert polluted["document_version_id"] is None

    ThemeService(ResearchRepository(cmd_session)).apply_theme_tags(
        case=cmd_session.get(ResearchCase, attacker_case_id),
        desired=["算力国产化"],
    )
    cmd_session.commit()
    theme = _request_as(
        cmd_client,
        attacker,
        "GET",
        "/api/v1/themes/算力国产化",
    )
    assert theme.status_code == 200
    polluted_theme_role = next(
        item
        for item in theme.json()["company_roles"]
        if item["role"] == "legacy polluted backlink"
    )
    assert polluted_theme_role["statement_id"] is None
