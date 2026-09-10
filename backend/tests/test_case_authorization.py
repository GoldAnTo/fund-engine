"""Permission semantics for the single Case authorization boundary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from app.models.events import DomainEvent
from app.models.identity import CaseAccessGrant, ResearchUser
from app.models.ledger import CaseTenantAdmission, ResearchCase
from app.security.principal import ResearchPrincipal
from app.services.case_authorization import CaseAuthorizationService
from app.services.event_research import EventResearchService
from app.schemas.v1.event_research import CreateEventResearchRequest


NOW = datetime.now(UTC)


def _user(session, *, tenant: str, label: str) -> ResearchUser:
    user = ResearchUser(
        issuer="https://issuer.example/realms/research",
        subject=f"{tenant}:{label}:{uuid.uuid4()}",
        tenant_id=tenant,
        display_name=label,
        normalized_email=None,
        active=True,
        last_seen_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _principal(user: ResearchUser, *roles: str) -> ResearchPrincipal:
    return ResearchPrincipal(
        user_id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        roles=frozenset(roles),
        expires_at=NOW + timedelta(hours=1),
    )


def _admit(session, research_case, document, *, tenant: str) -> None:
    session.add(
        CaseTenantAdmission(
            research_case_id=research_case.id,
            tenant_id=tenant,
            initial_document_version_id=document.id,
            admitted_by="test:fixture",
            admitted_at=NOW,
        )
    )
    session.flush()


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("owner", {"view", "edit", "review", "admin"}),
        ("editor", {"view", "edit"}),
        ("reviewer", {"view", "review"}),
        ("viewer", {"view"}),
    ],
)
def test_explicit_grants_have_one_stable_permission_matrix(
    session, research_case, document, role, allowed
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    user = _user(session, tenant="tenant-a", label=role)
    session.add(
        CaseAccessGrant(
            research_case_id=research_case.id,
            user_id=user.id,
            role=role,
            granted_by_principal_id="test:fixture",
            reason="matrix fixture",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    service = CaseAuthorizationService(session)
    principal = _principal(user)

    for permission in ("view", "edit", "review", "admin"):
        if permission in allowed:
            assert service.require(research_case.id, principal, permission).role == role
        else:
            with pytest.raises(PermissionDeniedError):
                service.require(research_case.id, principal, permission)


def test_ungranted_and_cross_tenant_cases_are_hidden_but_insufficient_action_is_403(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    viewer = _user(session, tenant="tenant-a", label="viewer")
    ungranted = _user(session, tenant="tenant-a", label="ungranted")
    foreign = _user(session, tenant="tenant-b", label="foreign")
    session.add(
        CaseAccessGrant(
            research_case_id=research_case.id,
            user_id=viewer.id,
            role="viewer",
            granted_by_principal_id="test:fixture",
            reason=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    service = CaseAuthorizationService(session)

    with pytest.raises(PermissionDeniedError):
        service.require(research_case.id, _principal(viewer), "edit")
    with pytest.raises(NotFoundError):
        service.require(research_case.id, _principal(ungranted), "view")
    with pytest.raises(NotFoundError):
        service.require(
            research_case.id,
            _principal(foreign, "tenant_administrator"),
            "view",
        )


def test_legacy_case_is_visible_only_to_tenant_administrator(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    ordinary = _principal(_user(session, tenant="tenant-a", label="ordinary"))
    tenant_admin = _principal(
        _user(session, tenant="tenant-a", label="tenant-admin"),
        "tenant_administrator",
    )
    service = CaseAuthorizationService(session)

    with pytest.raises(NotFoundError):
        service.require(research_case.id, ordinary, "view")
    assert service.require(research_case.id, tenant_admin, "admin").role is None
    for permission in ("edit", "review"):
        with pytest.raises(NotFoundError):
            service.require(research_case.id, tenant_admin, permission)


def test_legacy_case_authorization_is_resolved_by_one_database_statement(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    tenant_admin = _principal(
        _user(session, tenant="tenant-a", label="single-query-admin"),
        "tenant_administrator",
    )
    statements: list[str] = []

    def record_statement(*args) -> None:
        statements.append(args[2])

    event.listen(session.get_bind(), "before_cursor_execute", record_statement)
    try:
        decision = CaseAuthorizationService(session).require(
            research_case.id,
            tenant_admin,
            "admin",
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record_statement)

    assert decision.role is None
    assert len(statements) == 1


def test_tenant_administrator_can_grant_a_legacy_case(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    administrator = _principal(
        _user(session, tenant="tenant-a", label="tenant-admin"),
        "tenant_administrator",
    )
    target = _user(session, tenant="tenant-a", label="target")

    grant = CaseAuthorizationService(session).grant(
        research_case.id,
        administrator,
        target.id,
        "owner",
        reason="legacy Case assignment",
    )

    assert grant.role == "owner"
    assert grant.granted_by_principal_id == administrator.actor


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_first_legacy_case_grant_must_establish_an_owner(
    session, research_case, document, role
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    administrator = _principal(
        _user(session, tenant="tenant-a", label="tenant-admin"),
        "tenant_administrator",
    )
    target = _user(session, tenant="tenant-a", label="target")

    with pytest.raises(ValidationFailedError, match="first Case grant must be owner"):
        CaseAuthorizationService(session).grant(
            research_case.id,
            administrator,
            target.id,
            role,
            reason="legacy Case assignment",
        )

    assert session.scalar(
        select(CaseAccessGrant).where(
            CaseAccessGrant.research_case_id == research_case.id
        )
    ) is None


def test_last_owner_cannot_revoke_self(session, research_case, document) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    owner = _user(session, tenant="tenant-a", label="only-owner")
    principal = _principal(owner)
    grant = CaseAccessGrant(
        research_case_id=research_case.id,
        user_id=owner.id,
        role="owner",
        granted_by_principal_id="test:fixture",
        reason="only owner",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(grant)
    session.flush()

    with pytest.raises(ValidationFailedError, match="last owner"):
        CaseAuthorizationService(session).revoke(
            research_case.id,
            principal,
            owner.id,
            reason="self revoke",
        )

    assert session.get(CaseAccessGrant, grant.id).role == "owner"


def test_tenant_administrator_cannot_manage_a_case_after_real_grants_exist(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    owner = _user(session, tenant="tenant-a", label="only-owner")
    administrator = _principal(
        _user(session, tenant="tenant-a", label="tenant-admin"),
        "tenant_administrator",
    )
    grant = CaseAccessGrant(
        research_case_id=research_case.id,
        user_id=owner.id,
        role="owner",
        granted_by_principal_id="test:fixture",
        reason="only owner",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(grant)
    session.flush()

    with pytest.raises(NotFoundError):
        CaseAuthorizationService(session).change_role(
            research_case.id,
            administrator,
            owner.id,
            "editor",
            reason="unsafe downgrade",
        )

    assert session.get(CaseAccessGrant, grant.id).role == "owner"


def test_service_principal_has_no_tenant_wide_bypass(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    service_user = _user(session, tenant="tenant-a", label="service")
    principal = _principal(service_user, "service_principal")

    with pytest.raises(NotFoundError):
        CaseAuthorizationService(session).require(
            research_case.id, principal, "view"
        )


@pytest.mark.parametrize("principal_roles", [(), ("service_principal",)])
def test_public_grant_cannot_claim_a_zero_grant_case(
    session, document, principal_roles
) -> None:
    claimant = _user(session, tenant="tenant-a", label="claimant")
    principal = _principal(claimant, *principal_roles)
    legacy_case = ResearchCase(
        title="Zero-grant Case",
        industry_topic="authorization",
        created_at=NOW,
        created_by=principal.actor,
    )
    session.add(legacy_case)
    session.flush()
    _admit(session, legacy_case, document, tenant="tenant-a")

    with pytest.raises(NotFoundError):
        CaseAuthorizationService(session).grant(
            legacy_case.id,
            principal,
            principal.user_id,
            "owner",
            reason="attempted self-claim",
        )

    assert session.scalar(
        select(CaseAccessGrant).where(
            CaseAccessGrant.research_case_id == legacy_case.id
        )
    ) is None


def test_grant_role_change_and_revoke_append_audit_events(
    session, research_case, document
) -> None:
    owner = _user(session, tenant="tenant-a", label="owner")
    target = _user(session, tenant="tenant-a", label="target")
    principal = _principal(owner)
    owned_case = ResearchCase(
        title="Owned Case",
        industry_topic="authorization",
        created_at=NOW,
        created_by=principal.actor,
    )
    session.add(owned_case)
    session.flush()
    _admit(session, owned_case, document, tenant="tenant-a")
    service = CaseAuthorizationService(session)
    session.add(
        CaseAccessGrant(
            research_case_id=owned_case.id,
            user_id=owner.id,
            role="owner",
            granted_by_principal_id="test:fixture",
            reason="existing owner",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    grant = service.grant(
        owned_case.id,
        principal,
        target.id,
        "viewer",
        reason="read access",
    )
    assert grant.role == "viewer"
    assert service.change_role(
        owned_case.id,
        principal,
        target.id,
        "reviewer",
        reason="review duty",
    ).role == "reviewer"
    service.revoke(
        owned_case.id,
        principal,
        target.id,
        reason="duty ended",
    )
    session.flush()

    assert session.get(CaseAccessGrant, grant.id) is None
    events = list(
        session.query(DomainEvent)
        .filter(DomainEvent.aggregate_type == "case_access_grant")
        .order_by(DomainEvent.seq)
    )
    assert [event.type for event in events] == [
        "case_access_granted",
        "case_access_role_changed",
        "case_access_revoked",
    ]
    assert all(event.ref_id == str(owned_case.id) for event in events)
    assert all(event.actor == principal.actor for event in events)


def test_owner_can_change_and_revoke_an_inactive_collaborator(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    owner = _user(session, tenant="tenant-a", label="owner")
    target = _user(session, tenant="tenant-a", label="inactive-target")
    principal = _principal(owner)
    session.add(
        CaseAccessGrant(
            research_case_id=research_case.id,
            user_id=owner.id,
            role="owner",
            granted_by_principal_id="test:fixture",
            reason="existing owner",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = CaseAuthorizationService(session)
    target_grant = service.grant(
        research_case.id,
        principal,
        target.id,
        "viewer",
        reason="temporary read access",
    )
    target.active = False
    session.flush()

    changed = service.change_role(
        research_case.id,
        principal,
        target.id,
        "reviewer",
        reason="cleanup remains possible after deactivation",
    )
    assert changed.role == "reviewer"

    service.revoke(
        research_case.id,
        principal,
        target.id,
        reason="inactive account removed",
    )
    session.flush()
    assert session.get(CaseAccessGrant, target_grant.id) is None


def test_inactive_owner_does_not_satisfy_the_last_active_owner_guard(
    session, research_case, document
) -> None:
    _admit(session, research_case, document, tenant="tenant-a")
    active_owner = _user(session, tenant="tenant-a", label="active-owner")
    inactive_owner = _user(session, tenant="tenant-a", label="inactive-owner")
    inactive_owner.active = False
    session.add_all(
        [
            CaseAccessGrant(
                research_case_id=research_case.id,
                user_id=user.id,
                role="owner",
                granted_by_principal_id="test:fixture",
                reason="owner fixture",
                created_at=NOW,
                updated_at=NOW,
            )
            for user in (active_owner, inactive_owner)
        ]
    )
    session.flush()
    service = CaseAuthorizationService(session)

    with pytest.raises(ValidationFailedError, match="last owner"):
        service.change_role(
            research_case.id,
            _principal(active_owner),
            active_owner.id,
            "editor",
            reason="would strand the Case behind an inactive owner",
        )

    service.revoke(
        research_case.id,
        _principal(active_owner),
        inactive_owner.id,
        reason="inactive owner can be cleaned up safely",
    )


def test_new_event_case_has_owner_grant_before_the_creation_commit(
    session, monkeypatch
) -> None:
    creator = _user(session, tenant="tenant-a", label="creator")
    principal = _principal(creator)
    real_commit = session.commit
    commit_observations: list[tuple[CaseTenantAdmission, CaseAccessGrant]] = []

    def assert_owner_before_commit() -> None:
        admission = session.scalar(select(CaseTenantAdmission))
        grant = session.scalar(select(CaseAccessGrant))
        assert admission is not None
        assert grant is not None
        assert grant.research_case_id == admission.research_case_id
        assert grant.user_id == principal.user_id
        assert grant.role == "owner"
        commit_observations.append((admission, grant))
        real_commit()

    monkeypatch.setattr(session, "commit", assert_owner_before_commit)
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Issuer disclosed a material capacity change.",
            event_title="Capacity change",
            research_question="Does the capacity change alter earnings?",
            candidate_factors=["Demand", "Utilization", "Pricing"],
        ),
        principal=principal,
    )

    assert len(commit_observations) == 1
    case_id = uuid.UUID(created.case_id)
    research_case = session.get(ResearchCase, case_id)
    grant = session.scalar(
        select(CaseAccessGrant).where(
            CaseAccessGrant.research_case_id == case_id,
            CaseAccessGrant.user_id == principal.user_id,
        )
    )
    assert research_case is not None
    assert research_case.created_by == principal.actor
    assert grant is not None and grant.role == "owner"
    event = session.scalar(
        select(DomainEvent).where(
            DomainEvent.type == "case_access_granted",
            DomainEvent.aggregate_id == str(grant.id),
        )
    )
    assert event is not None
    assert event.actor == principal.actor


def test_forged_tenant_and_user_actor_cannot_create_a_case_or_grant(session) -> None:
    creator = _user(session, tenant="tenant-a", label="forgery-target")
    payload = CreateEventResearchRequest(
        raw_input="Forged request.",
        event_title="Forged creator",
        research_question="Can actor text mint access?",
        candidate_factors=["A", "B", "C"],
    )

    with pytest.raises(TypeError):
        EventResearchService(session).create(
            payload,
            tenant_id="tenant-b",
            actor=f"user:{creator.id}",
        )

    assert session.scalar(
        select(ResearchCase).where(ResearchCase.title == payload.event_title)
    ) is None
    assert session.scalar(select(CaseAccessGrant)) is None
