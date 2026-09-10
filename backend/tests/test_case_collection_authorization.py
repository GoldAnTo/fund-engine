"""API proofs that Case collections use grants, not tenant membership."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import uuid

from sqlalchemy import delete, select

from app.api.v1.tenant_context import require_research_actor
from app.main import app
from app.models.event_research import CaseRelation
from app.models.identity import CaseAccessGrant
from app.models.operational import ResearchRun
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.security.principal import ResearchPrincipal
from app.services.case_authorization import CaseAuthorizationService
from app.services.event_research import EventResearchService
from app.services.jobs import JobService
from app.repositories.proposals import ProposalRepository
from tests.research_identity import persist_research_principal


def _create_case(
    session,
    *,
    principal: ResearchPrincipal,
    label: str,
) -> uuid.UUID:
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input=f"Frozen source for {label}.",
            event_title=f"Collection authorization {label}",
            research_question=f"What changed for {label}?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=principal,
    )
    return uuid.UUID(created.case_id)


def _request_as(
    cmd_client,
    principal: ResearchPrincipal,
    method: str,
    path: str,
    *,
    body: dict | None = None,
):
    previous = app.dependency_overrides.get(require_research_actor)
    app.dependency_overrides[require_research_actor] = lambda: principal
    try:
        return cmd_client.request(method, path, json=body)
    finally:
        if previous is None:
            app.dependency_overrides.pop(require_research_actor, None)
        else:
            app.dependency_overrides[require_research_actor] = previous


def _get_as(cmd_client, principal: ResearchPrincipal, path: str):
    return _request_as(cmd_client, principal, "GET", path)


def _case_ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["case_id"] for item in response.json()["items"]}


def _legacy_case_ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _run_case_ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["case_id"] for item in response.json()["items"]}


def test_authorized_case_ids_applies_the_requested_permission(cmd_session) -> None:
    owner = persist_research_principal(
        cmd_session, tenant_id="team-a", label="permission-owner"
    )
    editor = persist_research_principal(
        cmd_session, tenant_id="team-a", label="permission-editor"
    )
    reviewer = persist_research_principal(
        cmd_session, tenant_id="team-a", label="permission-reviewer"
    )
    viewer = persist_research_principal(
        cmd_session, tenant_id="team-a", label="permission-viewer"
    )
    case_id = _create_case(cmd_session, principal=owner, label="role-matrix")
    service = CaseAuthorizationService(cmd_session)
    service.grant(case_id, owner, editor.user_id, "editor")
    service.grant(case_id, owner, reviewer.user_id, "reviewer")
    service.grant(case_id, owner, viewer.user_id, "viewer")
    cmd_session.commit()

    def visible(principal: ResearchPrincipal, permission: str = "view") -> set[uuid.UUID]:
        return set(
            cmd_session.scalars(
                service.authorized_case_ids(principal, permission=permission)
            )
        )

    assert visible(owner, "admin") == {case_id}
    assert visible(editor, "edit") == {case_id}
    assert visible(editor, "review") == set()
    assert visible(reviewer, "review") == {case_id}
    assert visible(reviewer, "edit") == set()
    assert visible(viewer) == {case_id}
    assert visible(viewer, "edit") == set()


def test_case_collections_only_return_cases_authorized_for_the_principal(
    cmd_client,
    cmd_session,
) -> None:
    researcher = persist_research_principal(
        cmd_session, tenant_id="team-a", label="researcher"
    )
    other_owner = persist_research_principal(
        cmd_session, tenant_id="team-a", label="other-owner"
    )
    foreign_owner = persist_research_principal(
        cmd_session, tenant_id="team-b", label="foreign-owner"
    )
    tenant_admin = replace(
        persist_research_principal(
            cmd_session, tenant_id="team-a", label="tenant-admin"
        ),
        roles=frozenset({"tenant_administrator"}),
    )
    service_principal = replace(
        persist_research_principal(
            cmd_session, tenant_id="team-a", label="service-principal"
        ),
        roles=frozenset({"service_principal"}),
    )

    owned_case = _create_case(
        cmd_session, principal=researcher, label="owned"
    )
    viewer_case = _create_case(
        cmd_session, principal=other_owner, label="viewer-granted"
    )
    ungranted_case = _create_case(
        cmd_session, principal=other_owner, label="same-tenant-ungranted"
    )
    legacy_case = _create_case(
        cmd_session, principal=other_owner, label="legacy-without-grants"
    )
    foreign_case = _create_case(
        cmd_session, principal=foreign_owner, label="foreign"
    )
    CaseAuthorizationService(cmd_session).grant(
        viewer_case,
        other_owner,
        researcher.user_id,
        "viewer",
        reason="collection visibility proof",
    )
    cmd_session.execute(
        delete(CaseAccessGrant).where(
            CaseAccessGrant.research_case_id == legacy_case
        )
    )

    now = datetime.now(UTC)
    runs_by_case: dict[uuid.UUID, ResearchRun] = {}
    for case_id in (
        owned_case,
        viewer_case,
        ungranted_case,
        legacy_case,
        foreign_case,
    ):
        run = ResearchRun(
            research_case_id=case_id,
            status="queued",
            stage="planning",
            round=0,
            max_rounds=1,
            budget=1,
            budget_used=0,
            scope_thesis_ids=[],
            monitor_version_id=None,
            created_at=now,
            updated_at=now,
        )
        cmd_session.add(run)
        runs_by_case[case_id] = run
    jobs_by_case = {
        case_id: JobService(cmd_session).create(
            kind="case-authorization-proof",
            research_case_id=case_id,
        )
        for case_id in (owned_case, viewer_case, ungranted_case, legacy_case)
    }
    visible_relation = CaseRelation(
        source_case_id=owned_case,
        target_case_id=viewer_case,
        relation_type="shared_driver",
        reason="both endpoints are visible",
        created_by=researcher.actor,
        review_state="reviewed",
        created_at=now,
    )
    hidden_relation = CaseRelation(
        source_case_id=owned_case,
        target_case_id=ungranted_case,
        relation_type="shared_driver",
        reason="one endpoint is not visible",
        created_by=researcher.actor,
        review_state="reviewed",
        created_at=now,
    )
    cmd_session.add_all([visible_relation, hidden_relation])
    cmd_session.commit()

    expected_user_cases = {str(owned_case), str(viewer_case)}
    expected_admin_cases = {str(legacy_case)}

    assert _case_ids(
        _get_as(cmd_client, researcher, "/api/v1/event-research")
    ) == expected_user_cases
    assert _case_ids(
        _get_as(cmd_client, tenant_admin, "/api/v1/event-research")
    ) == expected_admin_cases
    assert _case_ids(
        _get_as(cmd_client, service_principal, "/api/v1/event-research")
    ) == set()
    assert _legacy_case_ids(
        _get_as(cmd_client, researcher, "/api/v1/research-cases")
    ) == expected_user_cases
    assert _legacy_case_ids(
        _get_as(cmd_client, tenant_admin, "/api/v1/research-cases")
    ) == expected_admin_cases
    assert _legacy_case_ids(
        _get_as(cmd_client, service_principal, "/api/v1/research-cases")
    ) == set()

    network = _get_as(cmd_client, researcher, "/api/v1/event-research/network")
    assert network.status_code == 200, network.text
    assert {
        relation["id"] for relation in network.json()["reviewed_relations"]
    } == {str(visible_relation.id)}
    scoped_network = _get_as(
        cmd_client,
        researcher,
        f"/api/v1/event-research/{owned_case}/relations",
    )
    assert scoped_network.status_code == 200, scoped_network.text
    assert {
        relation["id"]
        for relation in scoped_network.json()["reviewed_relations"]
    } == {str(visible_relation.id)}

    for path in ("/api/v1/research-runs/active", "/api/v1/research-runs"):
        assert _run_case_ids(
            _get_as(cmd_client, researcher, path)
        ) == expected_user_cases
        assert _run_case_ids(
            _get_as(cmd_client, tenant_admin, path)
        ) == expected_admin_cases
        assert _run_case_ids(
            _get_as(cmd_client, service_principal, path)
        ) == set()

    assert _get_as(
        cmd_client,
        researcher,
        f"/api/v1/research-runs/{runs_by_case[owned_case].id}",
    ).status_code == 200
    assert _get_as(
        cmd_client,
        researcher,
        f"/api/v1/research-runs/{runs_by_case[ungranted_case].id}",
    ).status_code == 404
    assert _request_as(
        cmd_client,
        researcher,
        "POST",
        f"/api/v1/research-runs/{runs_by_case[viewer_case].id}/cancel",
        body={"change_reason": "viewer must not cancel"},
    ).status_code == 403

    assert _get_as(
        cmd_client,
        researcher,
        f"/api/v1/jobs/{jobs_by_case[viewer_case].id}",
    ).status_code == 200
    assert _get_as(
        cmd_client,
        researcher,
        f"/api/v1/jobs/{jobs_by_case[ungranted_case].id}",
    ).status_code == 404
    assert _request_as(
        cmd_client,
        researcher,
        "POST",
        f"/api/v1/jobs/{jobs_by_case[viewer_case].id}/cancel",
    ).status_code == 403
    assert _get_as(
        cmd_client,
        tenant_admin,
        f"/api/v1/jobs/{jobs_by_case[legacy_case].id}",
    ).status_code == 200
    assert _get_as(
        cmd_client,
        service_principal,
        f"/api/v1/jobs/{jobs_by_case[owned_case].id}",
    ).status_code == 404

    persisted_cases = set(
        cmd_session.scalars(
            select(ResearchRun.research_case_id).where(
                ResearchRun.research_case_id.in_(
                    (
                        owned_case,
                        viewer_case,
                        ungranted_case,
                        legacy_case,
                        foreign_case,
                    )
                )
            )
        )
    )
    assert persisted_cases == {
        owned_case,
        viewer_case,
        ungranted_case,
        legacy_case,
        foreign_case,
    }


def test_proposal_limit_is_applied_after_case_authorization(
    cmd_client,
    cmd_session,
) -> None:
    reviewer = persist_research_principal(
        cmd_session,
        tenant_id="team-a",
        label="proposal-reviewer",
    )
    hidden_owner = persist_research_principal(
        cmd_session,
        tenant_id="team-a",
        label="proposal-hidden-owner",
    )
    visible_case = _create_case(
        cmd_session,
        principal=reviewer,
        label="proposal-visible",
    )
    hidden_case = _create_case(
        cmd_session,
        principal=hidden_owner,
        label="proposal-hidden",
    )
    proposals = ProposalRepository(cmd_session)
    for index in range(50):
        proposals.add_proposal(
            kind="authorization-proof",
            payload={"index": index},
            target_context={},
            proposed_by_type="ai",
            proposed_by_ref="test",
            research_case_id=hidden_case,
        )
    visible = proposals.add_proposal(
        kind="authorization-proof",
        payload={"visible": True},
        target_context={},
        proposed_by_type="ai",
        proposed_by_ref="test",
        research_case_id=visible_case,
    )
    cmd_session.commit()

    response = _get_as(
        cmd_client,
        reviewer,
        "/api/v1/review-proposals?kind=authorization-proof&limit=50",
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [str(visible.id)]
