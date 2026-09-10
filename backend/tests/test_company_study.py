"""Private dossier commands and immutable adoption, without provider calls."""
# ruff: noqa: F811 -- imported pytest fixture reused as test arguments

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update

from tests.test_research_gateway_api import gateway_client  # noqa: F401
from tests.test_research_gateway_service import ALICE, BOB, _gateway, _send

BASE = "/api/v1/company-studies"
BODY = {
    "name": "英伟达",
    "symbol": "NVDA",
    "market": "US",
    "focus": "收入、现金流与竞争格局",
}


def create(client, key="study-1"):
    return client.post(BASE, json=BODY, headers={"Idempotency-Key": key})


def test_private_create_is_durable_idempotent_and_does_not_call_provider(
    gateway_client,
):
    client, runtime = gateway_client
    response = create(client)
    assert response.status_code == 201
    study = response.json()
    assert set(study) == {
        "id",
        "name",
        "symbol",
        "market",
        "focus",
        "revision",
        "created_at",
        "updated_at",
    }
    assert study["revision"] == 0
    assert create(client).json() == study
    assert runtime.calls == []
    assert client.get(BASE).json() == {"studies": [study]}
    assert client.get(f"{BASE}/{study['id']}").json() == {
        "study": study,
        "activities": [],
        "revisions": [],
        "monitor": None,
    }
    changed = client.post(
        BASE,
        json={**BODY, "name": "另一家公司"},
        headers={"Idempotency-Key": "study-1"},
    )
    assert changed.status_code == 409


def test_company_routes_require_human_actor_and_reject_cross_owner(gateway_client):
    client, _ = gateway_client
    response = create(client)
    assert response.status_code == 201
    study_id = response.json()["id"]
    for token in ["bob-token", "foreign-token"]:
        assert client.get(
            BASE, headers={"Authorization": f"Bearer {token}"}
        ).json() == {"studies": []}
        assert (
            client.get(
                f"{BASE}/{study_id}", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{BASE}/{study_id}/activities",
                json={"kind": "event", "text": "新事件"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "foreign",
                },
            ).status_code
            == 404
        )
    assert (
        client.get(BASE, headers={"Authorization": "Bearer legacy-token"}).status_code
        == 403
    )
    client.headers.pop("Authorization")
    assert client.get(BASE).status_code == 401


def test_all_writes_require_key_and_forbid_body_actor(gateway_client):
    client, _ = gateway_client
    assert client.post(BASE, json=BODY).status_code == 422
    assert (
        client.post(
            BASE,
            json={**BODY, "subject_id": "bob"},
            headers={"Idempotency-Key": "actor"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE, json={**BODY, "name": "  "}, headers={"Idempotency-Key": "blank"}
        ).status_code
        == 422
    )
    study_id = create(client).json()["id"]
    routes = [
        ("/activities", {"kind": "event", "text": "test"}),
        ("/links", {"conversation_id": str(uuid4())}),
        (
            "/revisions",
            {"activity_id": str(uuid4()), "expected_revision": 0, "note": "test"},
        ),
        ("/monitor", {"status": "paused", "frequency": "daily", "focus": "变化"}),
        (f"/activities/{uuid4()}/retry", {}),
    ]
    for suffix, body in routes:
        assert client.post(f"{BASE}/{study_id}{suffix}", json=body).status_code == 422


def test_activity_kinds_queue_immediately_and_replay(gateway_client):
    client, runtime = gateway_client
    study_id = create(client).json()["id"]
    for kind in ["baseline", "event", "material", "refresh"]:
        body = {"kind": kind, "text": "核验新的来源与变化"}
        kwargs = {"json": body, "headers": {"Idempotency-Key": kind}}
        response = client.post(f"{BASE}/{study_id}/activities", **kwargs)
        assert response.status_code == 201
        activity = response.json()
        assert activity["status"] == "queued" and activity["conversation_id"] is None
        assert client.post(f"{BASE}/{study_id}/activities", **kwargs).json() == activity
    assert runtime.calls == []
    assert len(client.get(f"{BASE}/{study_id}").json()["activities"]) == 4


def test_link_checks_native_private_access(gateway_client, cmd_session):
    client, _ = gateway_client
    study_id = create(client).json()["id"]
    receipt = _send(_gateway(cmd_session))
    response = client.post(
        f"{BASE}/{study_id}/links",
        json={"conversation_id": str(receipt.conversation_id)},
        headers={"Idempotency-Key": "link"},
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "linked"
    assert response.json()["run_spec_id"] == str(receipt.run_spec_id)
    from app.services.research_gateway import ResearchGateway

    other = ResearchGateway(cmd_session).create_conversation(BOB)
    assert (
        client.post(
            f"{BASE}/{study_id}/links",
            json={"conversation_id": str(other.id)},
            headers={"Idempotency-Key": "foreign-link"},
        ).status_code
        == 404
    )


def test_adopt_requires_native_and_four_authorized_current_outputs(
    gateway_client, cmd_session
):
    from app.models.operational import ResearchRun
    from tests.test_research_team_read import complete_team

    client, _ = gateway_client
    study_id = create(client).json()["id"]
    spec, _, _ = complete_team(cmd_session)
    activity = client.post(
        f"{BASE}/{study_id}/links",
        json={"conversation_id": str(spec.conversation_id)},
        headers={"Idempotency-Key": "link"},
    ).json()
    native = cmd_session.get(ResearchRun, spec.native_run_id)
    native.status = "running"
    cmd_session.commit()
    args = {
        "json": {
            "activity_id": activity["id"],
            "expected_revision": 0,
            "note": "采用真实研究",
        },
        "headers": {"Idempotency-Key": "adopt"},
    }
    assert client.post(f"{BASE}/{study_id}/revisions", **args).status_code == 409
    native.status = "succeeded"
    cmd_session.commit()
    adopted = client.post(f"{BASE}/{study_id}/revisions", **args)
    assert adopted.status_code == 201
    assert adopted.json()["version"] == 1 and adopted.json()["team_revision"] == 1
    assert client.post(f"{BASE}/{study_id}/revisions", **args).json() == adopted.json()
    args["headers"]["Idempotency-Key"] = "stale"
    assert client.post(f"{BASE}/{study_id}/revisions", **args).status_code == 409
    from app.services.research_team import ResearchTeamService

    ResearchTeamService(cmd_session).message(
        ALICE,
        spec.conversation_id,
        spec.id,
        text="核验最新季度",
        recipient="finance",
        expected_revision=1,
        idempotency_key="new-team-version",
    )
    args["json"]["expected_revision"] = 1
    assert client.post(f"{BASE}/{study_id}/revisions", **args).status_code == 409
    detail = client.get(f"{BASE}/{study_id}").json()
    assert detail["revisions"] == [adopted.json()]
    assert detail["activities"][0]["status"] != "completed"
    from app.models.company_study import CompanyStudyRevision
    from app.models.ledger import ImmutableLedgerError

    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(update(CompanyStudyRevision).values(note="rewrite"))


def test_monitor_versions_are_append_only_and_daily_weekly_shanghai(
    gateway_client, cmd_session
):
    client, _ = gateway_client
    study_id = create(client).json()["id"]
    args = {
        "json": {"status": "active", "frequency": "daily", "focus": "收入变化"},
        "headers": {"Idempotency-Key": "monitor"},
    }
    first = client.post(f"{BASE}/{study_id}/monitor", **args)
    assert first.status_code == 201
    assert first.json()["version"] == 1
    assert client.post(f"{BASE}/{study_id}/monitor", **args).json() == first.json()
    args["json"]["status"] = "paused"
    args["headers"]["Idempotency-Key"] = "pause"
    paused = client.post(f"{BASE}/{study_id}/monitor", **args).json()
    assert paused["version"] == 2 and paused["next_due_at"] is None
    from app.models.company_study import CompanyStudyMonitor

    assert len(list(cmd_session.scalars(select(CompanyStudyMonitor)))) == 2
    from app.services.company_study import next_monitor_due

    now = datetime(2026, 9, 9, 11, 59, tzinfo=UTC)
    assert next_monitor_due(now, "daily") == datetime(2026, 9, 9, 12, tzinfo=UTC)
    assert next_monitor_due(now + timedelta(minutes=1), "daily") == datetime(
        2026, 9, 10, 12, tzinfo=UTC
    )
    assert next_monitor_due(now, "weekly") == datetime(2026, 9, 14, 12, tzinfo=UTC)
    with pytest.raises(Exception, match="immutable"):
        cmd_session.execute(
            text("UPDATE company_study_monitors SET focus = :focus"),
            {"focus": "rewrite"},
        )


def test_company_routes_are_absent_from_legacy_app():
    from app.main import app

    assert not any("/company-studies" in path for path in app.openapi()["paths"])


def test_new_activity_freezes_adopted_historical_judgment_after_team_moves_on(
    gateway_client, cmd_session
):
    from app.models.company_study import CompanyStudyActivity
    from app.services.research_team import ResearchTeamService
    from tests.test_research_team_read import complete_team

    client, _ = gateway_client
    study_id = create(client).json()["id"]
    spec, _, outputs = complete_team(cmd_session)
    activity = client.post(
        f"{BASE}/{study_id}/links",
        json={"conversation_id": str(spec.conversation_id)},
        headers={"Idempotency-Key": "link-old"},
    ).json()
    adopted = client.post(
        f"{BASE}/{study_id}/revisions",
        json={
            "activity_id": activity["id"],
            "expected_revision": 0,
            "note": "采用首轮研究",
        },
        headers={"Idempotency-Key": "adopt-old"},
    )
    assert adopted.status_code == 201
    ResearchTeamService(cmd_session).message(
        ALICE,
        spec.conversation_id,
        spec.id,
        text="现在研究新的收入期间",
        recipient="finance",
        expected_revision=1,
        idempotency_key="later-team",
    )
    response = client.post(
        f"{BASE}/{study_id}/activities",
        json={"kind": "event", "text": "订单指引下调，需要重新核验原判断"},
        headers={"Idempotency-Key": "new-event"},
    )
    assert response.status_code == 201
    from app.models.company_study import CompanyStudy
    from app.services.company_study import CompanyStudyService

    row = cmd_session.get(CompanyStudyActivity, UUID(response.json()["id"]))
    prompt = row.prompt
    context = CompanyStudyService(cmd_session).adopted_context(
        cmd_session.get(CompanyStudy, UUID(study_id)), row.context_revision
    )
    for role, output in outputs.items():
        assert str(output.id) in prompt
        assert f"{role}的证据判断" in str(context)
        assert f"{role}的证据判断" not in prompt
        assert output.evidence_manifest[0]["evidence_link_id"] in str(context)
    assert "变化" in prompt and "反证" in prompt
    assert "现在研究新的收入期间" not in str(context)
    assert len(prompt) <= 20000
