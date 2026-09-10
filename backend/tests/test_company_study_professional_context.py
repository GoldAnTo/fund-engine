"""The four roles receive exact adopted judgments beyond a concise intake question."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select, text

from app.models.company_study import CompanyStudyActivity
from app.models.research_gateway import ResearchRunSpec
from app.models.research_team import ProfessionalOutput, ProfessionalTask
from app.services.company_study import CompanyStudyService
from tests.test_company_study_worker import db  # noqa: F401
from tests.test_research_gateway_service import ALICE


def prepare_company_increment(database, *, delay_binding=False):
    from app.services.company_study_worker import CompanyStudyWorker
    from app.services.research_gateway import ResearchGateway
    from app.services.research_gateway_automatic_adapter import AutomaticResearchRuntime
    from tests.test_research_gateway_artifact_authorization import (
        complete_authorized_evidence,
    )
    from tests.test_research_gateway_automatic_adapter import Extractor
    from tests.test_research_team_read import complete_team

    class ConciseExtractor(Extractor):
        def extract(self, *, raw_input, source_url):
            return replace(
                super().extract(raw_input=raw_input, source_url=source_url),
                company_name="示例公司",
                research_question="新增订单与现金流是否同步改善？",
                candidate_factors=("订单规模", "现金回收", "毛利压力"),
            )

    with database.factory() as session:
        prior_spec, prior_links, prior_outputs = complete_team(session)
        service = CompanyStudyService(session)
        linked = service.link(
            ALICE,
            database.study_id,
            conversation_id=prior_spec.conversation_id,
            idempotency_key="old",
        )
        service.adopt(
            ALICE,
            database.study_id,
            activity_id=UUID(linked["id"]),
            expected_revision=0,
            note="采用首轮判断",
            idempotency_key="adopt",
        )
        activity = service.add_activity(
            ALICE,
            database.study_id,
            kind="event",
            text="公告了新订单与回款信息",
            idempotency_key="increment",
        )
        session.get(CompanyStudyActivity, database.activity_id).status = "failed"
        session.commit()
        prior_ids = [str(output.id) for output in prior_outputs.values()]
        old_links = [link.id for link in prior_links]
    runner = CompanyStudyWorker(
        database.factory,
        gateway_factory=lambda session: ResearchGateway(
            session, AutomaticResearchRuntime(session, extractor=ConciseExtractor())
        ),
    )
    if delay_binding:
        from app.services.company_study_worker import activity_intent_key

        assert runner.claim_next() is not None
        with database.factory() as session:
            row = session.get(CompanyStudyActivity, UUID(activity["id"]))
            result = ResearchGateway(
                session, AutomaticResearchRuntime(session, extractor=ConciseExtractor())
            ).send_message(
                ALICE, text=row.prompt, idempotency_key=activity_intent_key(row.id)
            )
            spec_id = result.run_spec_id
    else:
        assert runner.run_once()
        with database.factory() as session:
            spec_id = session.get(
                CompanyStudyActivity, UUID(activity["id"])
            ).run_spec_id
    with database.factory() as session:
        row = session.get(CompanyStudyActivity, UUID(activity["id"]))
        spec = session.get(ResearchRunSpec, spec_id)
        assert "的证据判断" not in str(spec.frozen_scope)
        receipt = SimpleNamespace(run_spec_id=spec.id, native_run_id=spec.native_run_id)
        new_spec, _, new_links = complete_authorized_evidence(session, receipt=receipt)
        return SimpleNamespace(
            spec_id=new_spec.id,
            prior_ids=prior_ids,
            old_links=old_links,
            new_links=[link.id for link in new_links],
        )


def test_each_role_receives_prior_judgments_separate_from_current_evidence(db):  # noqa: F811
    from tests.test_research_team_worker import make_worker

    fixture = prepare_company_increment(db)
    runner, provider = make_worker(db)
    assert all(runner.run_once() for _ in range(4))
    assert {call["role"] for call in provider.calls} == {
        "industry",
        "finance",
        "strategy",
        "quality",
    }
    for call in provider.calls:
        context = call["company_study_context"]
        assert context["context_revision"] == 1
        assert all(identifier in str(context) for identifier in fixture.prior_ids)
        assert "finance的证据判断" in str(context)
        assert "不得作为本轮已准入证据" in context["usage"]
        assert not {str(value) for value in fixture.old_links} & {
            item["evidence_link_id"] for item in call["evidence"]
        }


def test_prior_context_revocation_during_provider_prevents_publication(db):  # noqa: F811
    from tests.test_research_gateway_content import _contract
    from tests.test_research_team_worker import RoleProvider, make_worker

    fixture = prepare_company_increment(db)

    def revoke(_):
        from app.models.ledger import EvidenceLink

        with db.factory() as session:
            old_contract = _contract(
                session, session.get(EvidenceLink, fixture.old_links[0])
            )
            new_contract = _contract(
                session, session.get(EvidenceLink, fixture.new_links[0])
            )
            assert old_contract.id != new_contract.id
            session.execute(
                text("UPDATE source_contracts SET allow_display=0 WHERE id=:id"),
                {"id": old_contract.id.hex},
            )
            session.commit()

    runner, provider = make_worker(db, RoleProvider(callback=revoke))
    assert runner.run_once()
    assert len(provider.calls) == 1
    with db.factory() as session:
        assert not list(
            session.scalars(
                select(ProfessionalOutput).where(
                    ProfessionalOutput.run_spec_id == fixture.spec_id
                )
            )
        )
        tasks = list(
            session.scalars(
                select(ProfessionalTask).where(
                    ProfessionalTask.run_spec_id == fixture.spec_id
                )
            )
        )
        assert any(task.status == "blocked" for task in tasks)


def test_revoked_prior_context_withholds_published_descendants_without_message_copy(db):  # noqa: F811
    from app.models.ledger import EvidenceLink
    from app.services.research_gateway import ResearchGateway
    from app.services.research_team import ResearchTeamService
    from tests.test_research_gateway_content import _contract
    from tests.test_research_team_worker import RoleProvider, make_worker

    fixture = prepare_company_increment(db)
    marker = "finance的证据判断"
    runner, _ = make_worker(
        db, RoleProvider(mutate=lambda body: body.update(summary=marker))
    )
    assert all(runner.run_once() for _ in range(4))
    with db.factory() as session:
        spec = session.get(ResearchRunSpec, fixture.spec_id)
        before = ResearchTeamService(session).read(ALICE, spec.conversation_id, spec.id)
        assert all(task["output_state"] == "available" for task in before["tasks"])
        conversation = ResearchGateway(session).read_conversation(
            ALICE, spec.conversation_id
        )
        assert marker not in conversation.model_dump_json()
        contract = _contract(session, session.get(EvidenceLink, fixture.old_links[0]))
        session.execute(
            text("UPDATE source_contracts SET allow_display=0 WHERE id=:id"),
            {"id": contract.id.hex},
        )
        session.commit()
        after = ResearchTeamService(session).read(ALICE, spec.conversation_id, spec.id)
        assert all(
            task["output_state"] == "withheld" and task["output"] is None
            for task in after["tasks"]
        )
        assert marker not in str(after)
        assert (
            marker
            not in ResearchGateway(session)
            .read_conversation(ALICE, spec.conversation_id)
            .model_dump_json()
        )


def test_corrupted_context_cycle_withholds_outputs_without_recursing(db):  # noqa: F811
    from app.services.research_team import ResearchTeamService
    from tests.test_research_team_worker import make_worker

    fixture = prepare_company_increment(db)
    runner, _ = make_worker(db)
    assert all(runner.run_once() for _ in range(4))
    with db.factory() as session:
        activity = session.scalar(
            select(CompanyStudyActivity).where(
                CompanyStudyActivity.run_spec_id == fixture.spec_id,
                CompanyStudyActivity.kind == "event",
            )
        )
        service = CompanyStudyService(session)
        service.adopt(
            ALICE,
            db.study_id,
            activity_id=activity.id,
            expected_revision=1,
            note="采用第二轮",
            idempotency_key="adopt-second",
        )
        # Disposable corruption probe bypasses the guard explicitly, as other
        # repository authorization tests do. Normal writes cannot form this cycle.
        session.execute(text("DROP TRIGGER cs_frozen_company_study_activities"))
        session.execute(
            text("UPDATE company_study_activities SET context_revision=2 WHERE id=:id"),
            {"id": activity.id.hex},
        )
        session.commit()
        spec = session.get(ResearchRunSpec, fixture.spec_id)
        result = ResearchTeamService(session).read(ALICE, spec.conversation_id, spec.id)
        assert all(task["output_state"] == "withheld" for task in result["tasks"])


def test_roles_resolve_context_from_gateway_origin_before_activity_receipt_is_saved(db):  # noqa: F811
    from tests.test_research_team_worker import make_worker

    fixture = prepare_company_increment(db, delay_binding=True)
    with db.factory() as session:
        assert (
            session.scalar(
                select(CompanyStudyActivity.id).where(
                    CompanyStudyActivity.run_spec_id == fixture.spec_id
                )
            )
            is None
        )
    runner, provider = make_worker(db)
    assert all(runner.run_once() for _ in range(4))
    assert len(provider.calls) == 4
    for call in provider.calls:
        assert "finance的证据判断" in str(call["company_study_context"])
        assert all(
            identifier in str(call["company_study_context"])
            for identifier in fixture.prior_ids
        )
