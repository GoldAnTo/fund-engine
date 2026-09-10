"""Read-only, redacted external access to report Wiki projections."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import uuid

import pytest
from sqlalchemy import event, select, update


def test_embed_wiki_openapi_documents_only_the_get_operation() -> None:
    from app.main import app

    operations = app.openapi()["paths"]["/api/v1/report-research/{case_id}/embed/wiki"]

    assert set(operations) == {"get"}


def _create_report(cmd_client, *, title: str = "嵌入测试研报") -> tuple[uuid.UUID, uuid.UUID]:
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": title,
            "publisher": "测试券商",
            "source_url": "https://broker.example.test/private-report",
            "published_at": "2026-08-01T08:00:00Z",
            "content": "嵌入测试原文：未上市供应商甲是星海科技的供应商。",
        },
    )
    assert response.status_code == 201
    body = response.json()
    return uuid.UUID(body["case"]["id"]), uuid.UUID(body["document"]["id"])


def _append_explicit_reviewed_scope(
    cmd_session, *, case_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    """Prepare the post-intake scope required by a successful embed read."""
    from app.models.report_research import ReportRelation
    from app.services.report_research import ReportClaimExtractor, ReportResearchService

    claims = ReportClaimExtractor(cmd_session).extract(case_id)
    assert claims
    relations = list(
        cmd_session.scalars(
            select(ReportRelation).where(
                ReportRelation.claim_id.in_([claim.id for claim in claims])
            )
        )
    )
    ReportResearchService(cmd_session).append_scope(
        case_id,
        document_id,
        changed_by="embed-reviewer",
        change_summary="人工审核后允许嵌入图谱",
        selected_claim_ids=[claim.id for claim in claims],
        selected_relation_ids=[relation.id for relation in relations],
    )
    cmd_session.commit()


def _issue(cmd_session, case_id: uuid.UUID, *, origin: str = "https://embed.partner.test") -> str:
    from app.services.embed_access import EmbedAccessService

    result = EmbedAccessService(cmd_session).issue(
        research_case_id=case_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        allowed_origins=[origin],
        issued_by="embed-test",
    )
    cmd_session.commit()
    return result.token


def _preflight(
    cmd_client,
    case_id: uuid.UUID,
    *,
    origin: str = "https://embed.partner.test",
    method: str = "GET",
    requested_headers: str = "X-Embed-Token",
):
    return cmd_client.options(
        f"/api/v1/report-research/{case_id}/embed/wiki",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": requested_headers,
        },
    )


def test_embed_wiki_requires_a_header_token(cmd_client) -> None:
    response = cmd_client.get(f"/api/v1/report-research/{uuid.uuid4()}/embed/wiki")

    assert response.status_code == 401


def test_embed_preflight_runs_before_global_cors_and_allows_only_a_live_matching_grant(
    cmd_client, cmd_session
) -> None:
    case_id, document_id = _create_report(cmd_client)
    _append_explicit_reviewed_scope(
        cmd_session, case_id=case_id, document_id=document_id
    )
    token = _issue(cmd_session, case_id)

    preflight = _preflight(cmd_client, case_id)
    get_response = cmd_client.get(
        f"/api/v1/report-research/{case_id}/embed/wiki",
        headers={
            "X-Embed-Token": token,
            "Origin": "https://embed.partner.test",
        },
    )

    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "https://embed.partner.test"
    assert preflight.headers["access-control-allow-methods"] == "GET, HEAD"
    assert preflight.headers["access-control-allow-headers"] == "X-Embed-Token"
    assert preflight.headers["vary"] == (
        "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
    )
    assert preflight.headers["cache-control"] == "no-store"
    assert preflight.headers["referrer-policy"] == "no-referrer"
    assert preflight.headers["x-content-type-options"] == "nosniff"
    assert get_response.status_code == 200


def test_embed_preflight_middleware_does_not_change_non_embed_cors(cmd_client) -> None:
    response = cmd_client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://embed.partner.test"},
        {"Access-Control-Request-Method": "GET"},
    ],
)
def test_embed_path_rejects_incomplete_preflight_before_global_cors(
    cmd_client, cmd_session, headers: dict[str, str]
) -> None:
    case_id, _ = _create_report(cmd_client)
    _issue(cmd_session, case_id)

    response = cmd_client.options(
        f"/api/v1/report-research/{case_id}/embed/wiki", headers=headers
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("origin", "method", "requested_headers"),
    [
        ("https://untrusted.example.test", "GET", "X-Embed-Token"),
        ("http://localhost:5173", "POST", "X-Embed-Token"),
        ("http://localhost:5173", "GET", "X-Embed-Token, Content-Type"),
    ],
)
def test_embed_preflight_rejects_unapproved_origin_method_or_headers(
    cmd_client, cmd_session, origin: str, method: str, requested_headers: str
) -> None:
    case_id, _ = _create_report(cmd_client)
    _issue(cmd_session, case_id)

    response = _preflight(
        cmd_client,
        case_id,
        origin=origin,
        method=method,
        requested_headers=requested_headers,
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("mode", ["none", "expired", "revoked"])
def test_embed_preflight_rejects_missing_expired_or_revoked_grant(
    cmd_client, cmd_session, mode: str
) -> None:
    from app.models.report_research import EmbedGrant
    from app.services.embed_access import EmbedAccessService

    case_id, _ = _create_report(cmd_client)
    if mode == "expired":
        token = "expired-preflight-token"
        cmd_session.add(
            EmbedGrant(
                research_case_id=case_id,
                token_sha256=EmbedAccessService.token_digest(token),
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                allowed_origins=["https://embed.partner.test"],
                issued_by="embed-test",
            )
        )
        cmd_session.commit()
    elif mode == "revoked":
        token = _issue(cmd_session, case_id)
        grant = EmbedAccessService(cmd_session).require_read_only(
            token, case_id, "https://embed.partner.test"
        )
        EmbedAccessService(cmd_session).revoke(
            grant.id, revoked_by="embed-test", reason="预检撤销"
        )
        cmd_session.commit()

    response = _preflight(cmd_client, case_id)

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_embed_rejects_a_token_supplied_in_the_query_string(cmd_client, cmd_session) -> None:
    case_id, _ = _create_report(cmd_client)
    token = _issue(cmd_session, case_id)

    response = cmd_client.get(
        f"/api/v1/report-research/{case_id}/embed/wiki", params={"token": token}
    )

    assert response.status_code == 401


def test_embed_token_returns_redacted_read_only_graph_and_security_headers(
    cmd_client, cmd_session
) -> None:
    case_id, document_id = _create_report(cmd_client)
    _append_explicit_reviewed_scope(
        cmd_session, case_id=case_id, document_id=document_id
    )
    token = _issue(cmd_session, case_id)

    response = cmd_client.get(
        f"/api/v1/report-research/{case_id}/embed/wiki",
        headers={
            "X-Embed-Token": token,
            "Origin": "https://embed.partner.test",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["access-control-allow-origin"] == "https://embed.partner.test"
    assert response.headers["vary"] == "Origin"
    assert "https://broker.example.test/private-report" not in response.text
    assert "嵌入测试原文" not in response.text
    assert "embed-test" not in response.text
    assert str(document_id) not in response.text
    assert "source_locator" not in response.text
    assert "relation_id" not in response.text
    assert "claim_id" not in response.text
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        response.text,
        flags=re.IGNORECASE,
    )
    body = response.json()
    assert set(body) == {"nodes", "edges", "factors"}
    assert all(re.fullmatch(r"n[1-9][0-9]*", node["id"]) for node in body["nodes"])
    assert all(re.fullmatch(r"e[1-9][0-9]*", edge["id"]) for edge in body["edges"])
    assert all(
        edge["source_id"] in {node["id"] for node in body["nodes"]}
        and edge["target_id"] in {node["id"] for node in body["nodes"]}
        for edge in body["edges"]
    )
    assert str(document_id) not in " ".join(
        node.get("source_locator") or "" for node in response.json()["nodes"]
    )
    assert all(
        "report://" not in (node.get("source_locator") or "")
        for node in response.json()["nodes"]
    )


def test_embed_token_is_stored_only_as_a_digest(cmd_client, cmd_session) -> None:
    from app.models.report_research import EmbedGrant

    case_id, _ = _create_report(cmd_client)
    token = _issue(cmd_session, case_id)
    grant = cmd_session.scalar(select(EmbedGrant))

    assert grant is not None
    assert grant.token_sha256 != token
    assert len(grant.token_sha256) == 64
    assert all("token" not in column.name or column.name == "token_sha256" for column in EmbedGrant.__table__.columns)


@pytest.mark.parametrize(
    ("grant_origin", "browser_origin"),
    [
        ("https://embed.partner.test:443", "https://embed.partner.test"),
        ("http://embed.partner.test:80", "http://embed.partner.test"),
    ],
)
def test_embed_normalizes_default_origin_ports_for_get_and_preflight(
    cmd_client, cmd_session, grant_origin: str, browser_origin: str
) -> None:
    case_id, document_id = _create_report(cmd_client)
    _append_explicit_reviewed_scope(
        cmd_session, case_id=case_id, document_id=document_id
    )
    token = _issue(cmd_session, case_id, origin=grant_origin)

    preflight = _preflight(cmd_client, case_id, origin=browser_origin)
    response = cmd_client.get(
        f"/api/v1/report-research/{case_id}/embed/wiki",
        headers={"X-Embed-Token": token, "Origin": browser_origin},
    )

    assert preflight.status_code == 204
    assert response.status_code == 200


def test_embed_origin_normalization_preserves_ipv6_brackets_and_nondefault_ports() -> None:
    from app.services.embed_access import EmbedAccessService

    assert EmbedAccessService._normalize_origin("https://[2001:db8::1]:443") == (
        "https://[2001:db8::1]"
    )
    assert EmbedAccessService._normalize_origin("https://[2001:db8::1]:8443") == (
        "https://[2001:db8::1]:8443"
    )


def test_embed_rejects_different_nondefault_origin_port(cmd_client, cmd_session) -> None:
    case_id, _ = _create_report(cmd_client)
    token = _issue(cmd_session, case_id, origin="https://embed.partner.test:8443")

    assert _preflight(cmd_client, case_id, origin="https://embed.partner.test").status_code == 403
    assert cmd_client.get(
        f"/api/v1/report-research/{case_id}/embed/wiki",
        headers={"X-Embed-Token": token, "Origin": "https://embed.partner.test"},
    ).status_code == 403


def test_embed_preflight_filters_expired_and_revoked_grants_in_database(
    cmd_client, cmd_session
) -> None:
    from app.models.report_research import EmbedGrant, EmbedGrantRevocation
    from app.services.embed_access import EmbedAccessService

    case_id, _ = _create_report(cmd_client)
    _issue(cmd_session, case_id)
    for index in range(32):
        stale = EmbedGrant(
            research_case_id=case_id,
            token_sha256=EmbedAccessService.token_digest(f"expired-{index}"),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            allowed_origins=["https://embed.partner.test"],
            issued_by="embed-test",
        )
        cmd_session.add(stale)
    cmd_session.flush()
    for index in range(32):
        revoked = EmbedGrant(
            research_case_id=case_id,
            token_sha256=EmbedAccessService.token_digest(f"revoked-{index}"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            allowed_origins=["https://embed.partner.test"],
            issued_by="embed-test",
        )
        cmd_session.add(revoked)
        cmd_session.flush()
        cmd_session.add(
            EmbedGrantRevocation(
                embed_grant_id=revoked.id,
                revoked_by="embed-test",
                reason="query-shape",
            )
        )
    cmd_session.commit()
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(cmd_session.bind, "before_cursor_execute", capture)
    try:
        assert EmbedAccessService(cmd_session).allows_preflight(
            case_id, "https://embed.partner.test"
        )
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", capture)

    assert len(statements) == 1
    statement = statements[0].lower()
    assert "select embed_grants.allowed_origins" in statement
    assert "embed_grants.expires_at >" in statement
    assert "not (exists" in statement
    assert " in (" not in statement


def test_report_wiki_does_not_repeat_staticmethod_decorators() -> None:
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "queries" / "report_wiki.py").read_text()

    assert "@staticmethod\n    @staticmethod" not in source


@pytest.mark.parametrize("mode", ["invalid", "expired", "revoked"])
def test_embed_rejects_invalid_expired_and_revoked_tokens(
    cmd_client, cmd_session, mode: str
) -> None:
    from app.models.report_research import EmbedGrant
    from app.services.embed_access import EmbedAccessService

    case_id, _ = _create_report(cmd_client)
    if mode == "invalid":
        token = "not-a-valid-embed-token"
    elif mode == "expired":
        token = "expired-embed-token"
        cmd_session.add(
            EmbedGrant(
                research_case_id=case_id,
                token_sha256=EmbedAccessService.token_digest(token),
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                allowed_origins=["https://embed.partner.test"],
                issued_by="embed-test",
            )
        )
        cmd_session.commit()
    else:
        token = _issue(cmd_session, case_id)
        grant = EmbedAccessService(cmd_session).require_read_only(
            token, case_id, "https://embed.partner.test"
        )
        EmbedAccessService(cmd_session).revoke(
            grant.id, revoked_by="embed-test", reason="测试撤销"
        )
        cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/report-research/{case_id}/embed/wiki",
        headers={"X-Embed-Token": token},
    )

    assert response.status_code == 401


def test_embed_token_cannot_cross_case_and_rejects_disallowed_origin(
    cmd_client, cmd_session
) -> None:
    allowed_case_id, _ = _create_report(cmd_client, title="第一份研报")
    other_case_id, _ = _create_report(cmd_client, title="第二份研报")
    token = _issue(cmd_session, allowed_case_id)

    cross_case = cmd_client.get(
        f"/api/v1/report-research/{other_case_id}/embed/wiki",
        headers={"X-Embed-Token": token},
    )
    rejected_origin = cmd_client.get(
        f"/api/v1/report-research/{allowed_case_id}/embed/wiki",
        headers={
            "X-Embed-Token": token,
            "Origin": "https://untrusted.example.test",
        },
    )
    globally_allowed_but_grant_rejected = cmd_client.get(
        f"/api/v1/report-research/{allowed_case_id}/embed/wiki",
        headers={
            "X-Embed-Token": token,
            "Origin": "http://localhost:5173",
        },
    )
    missing_origin = cmd_client.get(
        f"/api/v1/report-research/{allowed_case_id}/embed/wiki",
        headers={"X-Embed-Token": token},
    )

    assert cross_case.status_code == 403
    assert rejected_origin.status_code == 403
    assert globally_allowed_but_grant_rejected.status_code == 403
    assert "access-control-allow-origin" not in globally_allowed_but_grant_rejected.headers
    assert missing_origin.status_code == 403
    assert "access-control-allow-origin" not in missing_origin.headers


def test_embed_route_accepts_only_get_and_head(cmd_client, cmd_session) -> None:
    case_id, document_id = _create_report(cmd_client)
    _append_explicit_reviewed_scope(
        cmd_session, case_id=case_id, document_id=document_id
    )
    token = _issue(cmd_session, case_id)
    url = f"/api/v1/report-research/{case_id}/embed/wiki"

    assert cmd_client.head(
        url,
        headers={
            "X-Embed-Token": token,
            "Origin": "https://embed.partner.test",
        },
    ).status_code == 200
    assert cmd_client.head(url, headers={"X-Embed-Token": token}).status_code == 403
    assert cmd_client.post(url, headers={"X-Embed-Token": token}).status_code == 405
    assert cmd_client.put(url, headers={"X-Embed-Token": token}).status_code == 405
    assert cmd_client.delete(url, headers={"X-Embed-Token": token}).status_code == 405


def test_embed_token_management_has_no_unauthenticated_http_route() -> None:
    from app.main import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert not any("embed-grant" in path for path in paths)


def test_embed_grants_and_revocations_are_append_only_audited(cmd_client, cmd_session) -> None:
    from app.models.ledger import ImmutableLedgerError
    from app.models.report_research import EmbedGrant, EmbedGrantRevocation
    from app.services.embed_access import EmbedAccessService

    case_id, _ = _create_report(cmd_client)
    token = _issue(cmd_session, case_id)
    grant = EmbedAccessService(cmd_session).require_read_only(
        token, case_id, "https://embed.partner.test"
    )
    revocation = EmbedAccessService(cmd_session).revoke(
        grant.id, revoked_by="reviewer-1", reason="嵌入已结束"
    )
    cmd_session.commit()

    assert cmd_session.get(EmbedGrantRevocation, revocation.id).reason == "嵌入已结束"
    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(
            update(EmbedGrant).where(EmbedGrant.id == grant.id).values(issued_by="changed")
        )
    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(
            update(EmbedGrantRevocation)
            .where(EmbedGrantRevocation.id == revocation.id)
            .values(reason="changed")
        )


@pytest.mark.pg_only
def test_postgres_embed_ledger_tables_have_database_immutability_triggers(engine) -> None:
    from sqlalchemy import text

    with engine.connect() as connection:
        trigger_rows = connection.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid IN ('embed_grants'::regclass, 'embed_grant_revocations'::regclass) "
                "AND NOT tgisinternal"
            )
        ).scalars()

    assert set(trigger_rows) == {
        "no_update_embed_grants",
        "no_delete_embed_grants",
        "no_update_embed_grant_revocations",
        "no_delete_embed_grant_revocations",
    }
