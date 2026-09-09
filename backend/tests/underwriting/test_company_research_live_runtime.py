import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.ai.client import LLMClient
from app.ai.company_research import CompanyResearchDraftGenerator
from app.models.ledger import AIRun, ValidationError
from app.models.operational import Job
from app.underwriting.domain.company_research import (
    CompanyResearchArtifactReference,
    CompanyResearchMemoArtifact,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import CompanyResearchDraft
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
)
from app.underwriting.services.company_research_live_runtime import (
    CompanyResearchLiveRuntime,
    live_research_context,
    read_live_draft,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchPublicationService,
)
from app.underwriting.services.company_research_sources import (
    CompanyResearchSourceCompiler,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService
from tests.underwriting import test_company_research_live_sources as source_test_support
from tests.underwriting.test_company_research_generator import _response

LIVE_NOW = source_test_support.NOW
capture_case = source_test_support.capture_case


def test_machine_memo_binds_research_draft_without_changing_legacy_bytes():
    from app.underwriting.domain.company_research import CompanyResearchDraftReference

    memo = CompanyResearchMemoArtifact(
        assessment_status="not_answerable",
        business_map_ref=CompanyResearchArtifactReference("business_map", "a" * 64),
        driver_map_ref=CompanyResearchArtifactReference("driver_map", "b" * 64),
        financial_bridge_ref=CompanyResearchArtifactReference(
            "financial_bridge", "c" * 64
        ),
        scenario_set_ref=CompanyResearchArtifactReference("scenario_set", "d" * 64),
        valuation_set_ref=None,
        gap_keys=(),
        strongest_counterevidence=(),
        next_verification_events=(),
    )
    legacy = CompanyResearchArtifactCodec.encode("memo", memo)
    assert "research_draft_ref" not in legacy
    reference = CompanyResearchDraftReference(uuid4(), "e" * 64, "f" * 64)
    bound = replace(memo, research_draft_ref=reference)
    payload = CompanyResearchArtifactCodec.encode("memo", bound)
    assert payload["research_draft_ref"]["id"] == str(reference.id)
    assert "markdown" not in payload
    assert CompanyResearchArtifactCodec.decode("memo", payload) == bound
    confirmed = replace(
        bound,
        candidate_status="human_confirmed",
        reviewer="human:local-user",
        markdown="研究依据和缺口。",
    )
    assert (
        CompanyResearchArtifactCodec.decode(
            "memo", CompanyResearchArtifactCodec.encode("memo", confirmed)
        ).research_draft_ref
        == reference
    )


def test_live_context_reads_authenticated_focus_and_cutoff(api_client, session):
    from app.underwriting.services.company_research_live_runtime import (
        live_research_context,
    )
    from tests.underwriting.test_company_research_api import BASE, NOW, _alphabet_id

    company_id = _alphabet_id(session)
    request = {
        "company_id": str(company_id),
        "cutoff_at": NOW.isoformat(),
        "user_focus": "云业务的现金流",
    }
    preview = api_client.post(f"{BASE}/preview", json=request).json()
    initialized = api_client.post(
        f"{BASE}/initializations",
        json={**request, "preview_hash": preview["preview_hash"]},
        headers={"Idempotency-Key": "live-context"},
    ).json()
    from app.underwriting.persistence.company_research_models import (
        CompanyResearchPreparation,
    )

    preparation = session.get(
        CompanyResearchPreparation, UUID(initialized["preparation"]["id"])
    )
    context = live_research_context(session, preparation)
    assert context["user_focus"] == request["user_focus"]
    assert context["request_hash"] == preparation.request_hash
    assert (
        context["cutoff_at"].isoformat().replace("+00:00", "Z") == preview["cutoff_at"]
    )


class OfflineCompanyProvider:
    """Exercise the real LLMClient's parsing and usage capture without HTTP."""

    def __init__(self):
        self.calls = []
        self.failure = None
        self.before_response = None

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.before_response is not None:
            self.before_response()
        if self.failure == "transport":
            raise TimeoutError("provider-secret-request-body")
        prompt = json.loads(kwargs["messages"][1]["content"])
        excerpt = next(
            item
            for item in prompt["excerpts"]
            if "Google Search and Google Cloud support customers." in item["text"]
        )
        fact = next(
            item for item in prompt["facts"] if item["raw_hash"] == excerpt["raw_hash"]
        )
        response = _response()
        for group, items in response.items():
            for item in items:
                quote = (
                    "Competition and capital expenditures affect returns."
                    if group == "counterevidence"
                    else "Google Search and Google Cloud support customers."
                )
                item["citations"] = [
                    {"excerpt_id": excerpt["excerpt_id"], "quote": quote}
                ]
                item["fact_keys"] = [fact["fact_key"]]
        if self.failure == "schema":
            response["business_analysis"][0]["citations"][0]["quote"] = (
                "A fabricated citation containing provider-secret-data."
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(response, ensure_ascii=False)
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=40, total_tokens=140
            ),
        )


@pytest.fixture
def offline_company_run(session, capture_case, monkeypatch, tmp_path):
    """Only transport/source bytes are fixtures; all boundaries run real code."""
    sources, evidence, _refs, fetcher, fetch_calls = capture_case
    fixture = load_alphabet_golden_case_fixture()
    test_hashes = {item["fact_key"]: item["raw_hash"] for item in evidence["facts"]}
    fixture = replace(
        fixture,
        facts=tuple(
            replace(fact, raw_hash=test_hashes[fact.fact_key]) for fact in fixture.facts
        ),
    )
    # The offline PDF has its own true hash. Keep the compiler's test evidence
    # and transport's source specification in agreement, without bypassing any
    # capture, source-review, generator, model or publication validator.
    monkeypatch.setattr(
        CompanyResearchSourceCompiler, "_load_fixture", staticmethod(lambda: fixture)
    )
    monkeypatch.setenv("COMPANY_RESEARCH_SOURCE_DIR", str(tmp_path))
    provider = OfflineCompanyProvider()
    client = LLMClient(
        model_version="offline-company-provider.v1",
        mock=False,
        max_attempts=1,
        client=SimpleNamespace(chat=SimpleNamespace(completions=provider)),
    )
    generator = CompanyResearchDraftGenerator(client)
    clock = SimpleNamespace(value=LIVE_NOW)

    def now():
        return clock.value

    def capture(evidence_payload, source_refs, **kwargs):
        return sources.capture_governed_alphabet_sources(
            evidence_payload, source_refs, fetcher=fetcher, **kwargs
        )

    from tests.underwriting.test_company_research_api import _alphabet_id

    initializer = CompanyResearchInitializer(session, now=now)
    company_id = _alphabet_id(session)
    focus = "云业务现金回报与资本开支节奏"
    preview = initializer.preview(
        company_id=company_id, cutoff_at=fixture.cutoff, user_focus=focus
    )
    initialized = initializer.initialize(
        company_id=company_id,
        cutoff_at=fixture.cutoff,
        user_focus=focus,
        preview_hash=preview.input_hash,
        idempotency_key="offline-live-company",
    )
    runtime = CompanyResearchLiveRuntime(
        generator=generator, storage_root=tmp_path, now=now, capture=capture
    )
    worker = CompanyResearchPreparationWorker(session, now=now, live_runtime=runtime)
    claim = worker.claim_next()
    assert claim is not None and claim.step == "evidence_index"
    session.commit()
    return SimpleNamespace(
        initialized=initialized,
        runtime=runtime,
        generator=generator,
        provider=provider,
        worker=worker,
        claim=claim,
        clock=clock,
        now=now,
        fetch_calls=fetch_calls,
        source_module=sources,
        storage_root=tmp_path,
    )


def _pending_live_workspace(session, run):
    assert run.worker.run_claim(run.claim) == "awaiting_evidence_review"
    session.commit()
    session.expire_all()
    workspace = CompanyResearchWorkbench(session, now=run.now).workspace(
        project_id=run.initialized.project.id
    )
    assert workspace.preparation.status == "awaiting_evidence_review"
    assert workspace.change_summary["reviewed_fact_count"] == 0
    assert workspace.research_draft is not None
    assert workspace.research_draft["candidate_status"] == "machine_draft"
    from app.underwriting.api.company_research_router import _workspace_response

    wire = _workspace_response(
        workspace, expected_project_id=run.initialized.project.id
    )
    assert wire.research_draft.content_hash == workspace.research_draft["content_hash"]
    assert len(run.fetch_calls) == 2
    assert len(run.provider.calls) == 1
    return workspace


def _publish_live_research(session, run):
    pending = _pending_live_workspace(session, run)
    workbench = CompanyResearchWorkbench(session, now=run.now)
    repository = CompanyResearchRepository(session)
    project_id = run.initialized.project.id
    evidence = repository.current_artifact(project_id, "evidence_index")
    assert evidence is not None
    for fact in tuple(evidence.payload["facts"]):
        evidence = workbench.review_evidence(
            project_id=project_id,
            evidence_artifact_id=evidence.id,
            fact_key=fact["fact_key"],
            decision="confirmed",
            expected_head_id=evidence.id,
        ).evidence_artifact
    run.clock.value += timedelta(minutes=1)
    model_worker = CompanyResearchPreparationWorker(session, now=run.now)
    claim = model_worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert model_worker.run_claim(claim) == "awaiting_judgment_review"
    workspace = workbench.workspace(project_id=project_id)
    memo = repository.current_artifact(project_id, "memo")
    assert memo is not None
    reference = memo.payload["research_draft_ref"]
    assert reference == {
        key: pending.research_draft[key]
        for key in ("id", "content_hash", "source_bundle_hash")
    }
    assert memo.payload["candidate_status"] == "machine_draft"
    assert "markdown" not in memo.payload
    run.clock.value += timedelta(minutes=1)
    publisher = CompanyResearchPublicationService(session, now=run.now)
    confirmation = publisher.confirm_judgment(
        project_id=project_id,
        expected_lock_version=workspace.draft.lock_version,
        expected_memo_id=memo.id,
        expected_memo_content_hash=memo.content_hash,
        markdown=pending.research_draft["markdown"],
    )
    confirmed = repository.current_artifact(project_id, "memo")
    assert confirmed.payload["research_draft_ref"] == reference
    assert confirmed.payload["reviewer"] == "human:local-user"
    preview = publisher.preview(
        project_id=project_id, expected_lock_version=confirmation.draft_lock_version
    )
    run.clock.value += timedelta(minutes=1)
    revision = publisher.publish(
        project_id=project_id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="offline-live-publish",
    )
    session.commit()
    return pending, publisher, revision


def test_offline_source_to_pending_draft_model_confirmation_and_frozen_replay(
    session, offline_company_run
):
    run = offline_company_run
    pending, publisher, revision = _publish_live_research(session, run)
    project_id = run.initialized.project.id
    assert pending.research_draft["user_focus"] == "云业务现金回报与资本开支节奏"
    assert "未经人工确认" in pending.research_draft["markdown"]
    assert pending.research_draft["usage"]["attempts"][0]["total_tokens"] == 140
    assert revision.memo_markdown == pending.research_draft["markdown"].rstrip()
    session.expire_all()
    assert publisher.revision(project_id, revision.id) == revision
    assert (
        "云业务现金回报与资本开支节奏"
        in publisher.export(project_id, revision.id).content
    )
    assert len(run.provider.calls) == 1
    assert len(run.fetch_calls) == 2
    assert len(tuple(session.scalars(select(CompanyResearchDraft)))) == 1
    audits = tuple(
        session.scalars(select(AIRun).where(AIRun.kind == "company_research_draft"))
    )
    assert len(audits) == 1 and audits[0].status == "success"
    assert audits[0].input_ref["input_hash"] == pending.research_draft["input_hash"]


@pytest.mark.parametrize("failure", ["transport", "schema"])
def test_failed_live_provider_keeps_input_receipt_and_usage_without_usable_draft(
    session, offline_company_run, failure
):
    run = offline_company_run
    run.provider.failure = failure
    assert run.worker.run_claim(run.claim) == "recoverable_failure"
    session.commit()
    session.expire_all()
    audit = session.scalar(select(AIRun).where(AIRun.kind == "company_research_draft"))
    assert audit is not None and audit.status == "failed"
    assert audit.model_version == "offline-company-provider.v1"
    assert audit.error == "company_research_live_generation_failed"
    assert (
        "provider-secret"
        not in json.dumps(audit.input_ref) + audit.output_summary + audit.error
    )
    receipt = audit.input_ref["source_bundle"]
    assert receipt["bundle_hash"] == audit.input_ref["source_bundle_hash"]
    context = live_research_context(session, run.initialized.preparation)
    compiled = CompanyResearchSourceCompiler().compile_evidence_index(
        run.worker._provider_input(run.initialized.preparation)
    )
    expected_input = run.generator.input_hash_for(
        **context,
        source_bundle=receipt,
        evidence_payload=compiled.evidence_index_payload,
    )
    assert audit.input_ref["input_hash"] == expected_input
    attempts = audit.usage["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["usage_state"] == (
        "unavailable" if failure == "transport" else "reported"
    )
    assert attempts[0]["total_tokens"] == (None if failure == "transport" else 140)
    assert read_live_draft(session, project_id=run.initialized.project.id) is None
    assert (
        CompanyResearchRepository(session).current_artifact(
            run.initialized.project.id, "evidence_index"
        )
        is None
    )
    assert session.get(Job, run.claim.job_id).status == "queued"
    assert len(run.provider.calls) == 1


def test_cancelled_claim_keeps_provider_usage_but_publishes_no_artifacts(
    session, offline_company_run
):
    run = offline_company_run

    def cancel_during_provider():
        session.get(Job, run.claim.job_id).cancel_requested = True
        session.commit()

    run.provider.before_response = cancel_during_provider
    assert run.worker.run_claim(run.claim) == "discarded"
    session.commit()
    session.expire_all()
    audit = session.scalar(select(AIRun).where(AIRun.kind == "company_research_draft"))
    assert audit is not None and audit.status == "success"
    assert audit.usage["attempts"][0]["total_tokens"] == 140
    assert read_live_draft(session, project_id=run.initialized.project.id) is None
    assert (
        CompanyResearchRepository(session).current_artifact(
            run.initialized.project.id, "evidence_index"
        )
        is None
    )
    assert session.get(Job, run.claim.job_id).status == "cancelled"


def test_frozen_live_revision_ignores_subsequent_current_workspace_scope_and_basis_edits(
    session, offline_company_run
):
    run = offline_company_run
    _pending, publisher, revision = _publish_live_research(session, run)
    project_id = run.initialized.project.id
    run.clock.value += timedelta(minutes=1)
    drafts = WorkspaceDraftService(session, now=run.now)
    current = drafts.read(project_id)
    drafts.save(
        project_id,
        expected_lock_version=current.lock_version,
        patch={
            "scope_id": None,
            "historical_basis_id": None,
            "user_focus": "后续草稿的其他重点",
        },
    )
    session.commit()
    session.expire_all()
    assert publisher.revision(project_id, revision.id) == revision
    assert len(run.provider.calls) == 1
    assert len(run.fetch_calls) == 2


def test_live_draft_read_rejects_rehashed_generation_context_substitution(
    session, offline_company_run
):
    from tests.underwriting.test_company_research_persistence import _tamper_row

    run = offline_company_run
    _pending_live_workspace(session, run)
    row = session.scalar(select(CompanyResearchDraft))
    changed = deepcopy(row.payload)
    changed["generation"]["user_focus"] = "替换为另一个研究重点"
    _tamper_row(
        session,
        CompanyResearchDraft,
        row.id,
        payload=changed,
        content_hash=canonical_hash(changed),
    )
    session.commit()
    session.expire_all()
    with pytest.raises(ValidationError):
        read_live_draft(session, project_id=run.initialized.project.id)


def test_expired_claim_cannot_publish_after_another_worker_takes_ownership(
    session, offline_company_run
):
    run = offline_company_run
    replacement = SimpleNamespace(claim=None)

    def recover_and_reclaim_during_provider():
        run.clock.value += timedelta(minutes=31)
        worker = CompanyResearchPreparationWorker(session, now=run.now)
        assert (
            worker.recover_stale_claims(before=run.now() - timedelta(minutes=30)) == 1
        )
        replacement.claim = worker.claim_next()
        assert replacement.claim is not None
        assert replacement.claim.claim_token != run.claim.claim_token
        session.commit()

    run.provider.before_response = recover_and_reclaim_during_provider
    assert run.worker.run_claim(run.claim) == "discarded"
    session.commit()
    session.expire_all()
    job = session.get(Job, run.claim.job_id)
    assert job.status == "running"
    assert job.claim_token == replacement.claim.claim_token
    audit = session.scalar(select(AIRun).where(AIRun.kind == "company_research_draft"))
    assert audit is not None and audit.usage["attempts"][0]["total_tokens"] == 140
    assert read_live_draft(session, project_id=run.initialized.project.id) is None
    assert (
        CompanyResearchRepository(session).current_artifact(
            run.initialized.project.id, "evidence_index"
        )
        is None
    )


def test_live_draft_read_refreshes_cached_evidence_before_validating_source_binding(
    session, offline_company_run
):
    from tests.underwriting.test_company_research_persistence import _tamper_row

    run = offline_company_run
    _pending_live_workspace(session, run)
    project_id = run.initialized.project.id
    cached_evidence = CompanyResearchRepository(session).current_artifact(
        project_id, "evidence_index"
    )
    assert cached_evidence is not None
    original_payload = deepcopy(cached_evidence.payload)
    changed_payload = deepcopy(original_payload)
    changed_payload["facts"][0]["value"] = "999999"
    _tamper_row(
        session,
        type(cached_evidence),
        cached_evidence.id,
        expire=False,
        payload=changed_payload,
    )

    # Keep the identity-map object alive and stale until the public read refreshes it.
    assert cached_evidence.payload == original_payload
    with pytest.raises(ValidationError, match="evidence binding is invalid"):
        read_live_draft(session, project_id=project_id)


def test_output_transaction_rollback_preserves_usage_and_removes_partial_draft(
    session, offline_company_run, monkeypatch
):
    from app.underwriting.services import (
        company_research_live_runtime as runtime_module,
    )

    run = offline_company_run
    original = runtime_module.persist_live_draft

    def fail_after_draft_flush(*args, **kwargs):
        original(*args, **kwargs)
        raise ValidationError("test output commit rejected")

    monkeypatch.setattr(runtime_module, "persist_live_draft", fail_after_draft_flush)
    assert run.worker.run_claim(run.claim) == "discarded"
    session.commit()
    session.expire_all()
    audits = tuple(
        session.scalars(select(AIRun).where(AIRun.kind == "company_research_draft"))
    )
    assert len(audits) == 1
    assert audits[0].status == "success"
    assert audits[0].usage["attempts"][0]["total_tokens"] == 140
    assert tuple(session.scalars(select(CompanyResearchDraft))) == ()
    repository = CompanyResearchRepository(session)
    assert (
        repository.current_artifact(run.initialized.project.id, "evidence_index")
        is None
    )
    assert (
        repository.current_artifact(run.initialized.project.id, "research_gaps") is None
    )
