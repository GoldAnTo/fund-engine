"""Gateway artifacts reauthorize immutable native source and temporal lineage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionJob,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    SourceSpan,
    SourceStatement,
)
from app.services.research_gateway_artifacts import native_artifacts
from sqlalchemy import select


def complete_authorized_evidence(session, *, expires_at=None, receipt=None):
    """Use real native source jobs and real deterministic admission/publication."""
    from app.api.v1.tenant_context import ResearchActor
    from app.models.operational import ResearchRun
    from app.models.research_gateway import ResearchRunSpec
    from app.services.atomic_claims import AtomicClaimService
    from app.services.automatic_admission import AutomaticAdmissionGate
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.research_gateway import ResearchGateway
    from app.services.research_gateway_automatic_adapter import AutomaticResearchRuntime

    from tests.test_automatic_admission import LEASE_TOKEN, _seed
    from tests.test_automatic_research_pipeline import _AssessmentGenerator
    from tests.test_research_gateway_automatic_adapter import Extractor

    class NamedCompanyExtractor(Extractor):
        def extract(self, *, raw_input, source_url):
            return replace(
                super().extract(raw_input=raw_input, source_url=source_url),
                company_name="示例公司",
            )

    receipt = receipt or ResearchGateway(
        session, AutomaticResearchRuntime(session, extractor=NamedCompanyExtractor())
    ).send_message(
        ResearchActor("team-a", frozenset(), "alice"),
        text="研究设备验证与收入兑现",
        conversation_id=None,
        idempotency_key="authorized-native-evidence",
    )
    spec = session.get(ResearchRunSpec, receipt.run_spec_id)
    run = session.get(ResearchRun, receipt.native_run_id)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    links = []
    for job in jobs:
        if job.request_snapshot["objective"] != "support":
            job.status = job.stage = "failed"
            continue
        metric = job.request_snapshot["metric_terms"][0]
        quote = f"示例公司2026年1月31日{metric}为100亿元。"
        source = _seed(
            session,
            allowed_source_roles=tuple(job.request_snapshot["allowed_source_roles"]),
            metric_terms=(metric,),
            predicate=metric,
            text=quote,
            raw_bytes=quote.encode(),
            observed_period="2026-01-31",
            normalized_text=quote.rstrip("。"),
            contract_effective_until=expires_at,
        )
        reference = _copy_row(source.reference, job_id=job.id)
        session.add(reference)
        session.flush()
        attempt = session.get(AcquisitionAttempt, source.artifact.attempt_id)
        attempt = _copy_row(
            attempt,
            job_id=job.id,
            safe_metadata={"source_reference_id": str(reference.id)},
        )
        session.add(attempt)
        session.flush()
        artifact = _copy_row(
            source.artifact, source_reference_id=reference.id, attempt_id=attempt.id
        )
        session.add(artifact)
        session.flush()
        binding = session.scalar(
            select(RetrievalArtifactDocument).where(
                RetrievalArtifactDocument.retrieval_artifact_id == source.artifact.id
            )
        )
        session.add(_copy_row(binding, retrieval_artifact_id=artifact.id))
        session.add(
            CaseDocumentVersion(
                research_case_id=run.research_case_id,
                document_version_id=source.document.id,
                linked_at=datetime.now(UTC),
            )
        )
        job.status, job.stage = "running", "admitting"
        job.lease_owner, job.lease_token = (
            "system:acquisition-worker@gateway-test#source",
            LEASE_TOKEN,
        )
        job.lease_expires_at = datetime.now(UTC) + timedelta(hours=1)
        session.flush()
        # The unused standalone fixture job is deliberately not bound to this
        # native run; only the native request above owns this source lineage.
        context = replace(
            source.context,
            job_id=job.id,
            thesis_id=job.thesis_id,
            retrieval_artifact_id=artifact.id,
            cutoff=datetime.fromisoformat(job.request_snapshot["cutoff"]),
            expected_subject=None,
        )
        decision = AutomaticAdmissionGate(session).evaluate(source.candidate, context)
        assert decision.outcome == "admitted", {
            key: value["facts"].get("failures")
            for key, value in decision.gate_results.items()
        }
        _, link = AtomicClaimService(session).publish_automatically(
            source.candidate.id,
            decision.id,
            lease_token=LEASE_TOKEN,
        )
        links.append(link)
        job.status = job.stage = "succeeded"
        job.admitted_count = 1
        job.lease_owner = job.lease_token = job.lease_expires_at = None
    assert pipeline.advance(run) == "completed"
    session.commit()
    return spec, run, links


def _copy_row(row, **overrides):
    return type(row)(
        **{
            **{
                column.name: getattr(row, column.name)
                for column in row.__table__.columns
                if column.name != "id"
            },
            **overrides,
        }
    )


def _evidence_document(session, link):
    statement = session.get(SourceStatement, link.source_statement_id)
    span = session.get(SourceSpan, statement.source_span_id)
    return session.get(DocumentVersion, span.document_version_id)


def test_valid_native_admission_exposes_evidence_and_verified_draft(cmd_session):
    spec, _, links = complete_authorized_evidence(cmd_session)
    refs = native_artifacts(cmd_session, spec)
    assert {ref.id for ref in refs if ref.kind == "evidence_link"} == {
        link.id for link in links
    }
    assert any(ref.kind == "draft" for ref in refs)


@pytest.mark.parametrize(
    "mutation", ["tenant", "cutoff", "request_roles", "policy_version", "policy_roles"]
)
def test_bound_job_cannot_widen_frozen_authority(cmd_session, mutation):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    job = cmd_session.scalar(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    )
    if mutation == "tenant":
        job.tenant_id = "another-tenant"
    elif mutation == "cutoff":
        job.request_snapshot = {
            **job.request_snapshot,
            "cutoff": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
        }
    elif mutation == "request_roles":
        job.request_snapshot = {
            **job.request_snapshot,
            "allowed_source_roles": ["user_provided_material"],
        }
    elif mutation == "policy_version":
        job.policy_snapshot = {**job.policy_snapshot, "version": "untrusted-v999"}
    else:
        job.policy_snapshot = {
            **job.policy_snapshot,
            "allowed_source_roles": ["user_provided_material"],
        }
    cmd_session.commit()
    assert not any(
        ref.kind in {"evidence_link", "document_version", "draft"}
        for ref in native_artifacts(cmd_session, spec)
    )


@pytest.mark.parametrize("field", ["published_at", "available_at"])
def test_future_evidence_is_withheld_and_invalidates_draft(cmd_session, field):
    from sqlalchemy import text

    spec, _, links = complete_authorized_evidence(cmd_session)
    document = _evidence_document(cmd_session, links[0])
    # Simulate persisted corruption outside the append-only ORM boundary.
    cmd_session.execute(
        text(f"UPDATE document_versions SET {field} = :future WHERE id = :id"),
        {
            "future": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "id": document.id.hex,
        },
    )
    cmd_session.commit()
    refs = native_artifacts(cmd_session, spec)
    assert links[0].id not in {ref.id for ref in refs}
    assert not any(ref.kind == "draft" for ref in refs)


@pytest.mark.parametrize("field", ["frozen_cutoff_json", "frozen_source_policy_json"])
def test_incomplete_frozen_authority_is_not_inferred_from_mutable_rows(
    cmd_session, field
):
    from sqlalchemy import text

    spec, _, _ = complete_authorized_evidence(cmd_session)
    cmd_session.execute(
        text(f"UPDATE research_run_specs SET {field} = :value WHERE id = :id"),
        {"value": "{}", "id": spec.id.hex},
    )
    cmd_session.commit()
    assert native_artifacts(cmd_session, spec) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "decision",
        "statement",
        "source_identity",
        "fetch_reference",
        "missing_publication",
    ],
)
def test_malformed_persisted_lineage_is_never_projected(cmd_session, mutation):
    import json

    from app.models.acquisition import AutomaticAdmissionDecision
    from sqlalchemy import text

    spec, _, links = complete_authorized_evidence(cmd_session)
    link = links[0]
    decision = cmd_session.get(
        AutomaticAdmissionDecision, link.automatic_admission_decision_id
    )
    artifact = cmd_session.get(RetrievalArtifact, decision.retrieval_artifact_id)
    if mutation == "decision":
        cmd_session.execute(
            text(
                "UPDATE automatic_admission_decisions SET gate_results = :value WHERE id = :id"
            ),
            {"value": "{}", "id": decision.id.hex},
        )
    elif mutation == "statement":
        cmd_session.execute(
            text(
                "UPDATE source_statements SET automatic_admission_decision_id = NULL WHERE id = :id"
            ),
            {"id": link.source_statement_id.hex},
        )
    elif mutation == "source_identity":
        reference = cmd_session.get(SourceReference, artifact.source_reference_id)
        cmd_session.execute(
            text("UPDATE source_references SET metadata_json = :value WHERE id = :id"),
            {
                "value": json.dumps({"provider_identity": "Untrusted Provider"}),
                "id": reference.id.hex,
            },
        )
    elif mutation == "fetch_reference":
        cmd_session.execute(
            text(
                "UPDATE acquisition_attempts SET safe_metadata = :value WHERE id = :id"
            ),
            {"value": "{}", "id": artifact.attempt_id.hex},
        )
    else:
        document = _evidence_document(cmd_session, link)
        cmd_session.execute(
            text("UPDATE document_versions SET published_at = NULL WHERE id = :id"),
            {"id": document.id.hex},
        )
    cmd_session.commit()
    refs = native_artifacts(cmd_session, spec)
    assert link.id not in {ref.id for ref in refs}
    assert not any(ref.kind == "draft" for ref in refs)


def test_frozen_original_material_without_publication_date_remains_authorized(
    cmd_session,
):
    import json

    from app.api.v1.tenant_context import ResearchActor
    from app.models.ledger import EvidenceLink
    from app.models.operational import ResearchRun
    from app.models.research_gateway import ResearchRunSpec
    from app.repositories.acquisition import AcquisitionRepository
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_extraction import EventExtraction
    from app.services.research_gateway import ResearchGateway
    from app.services.research_gateway_automatic_adapter import AutomaticResearchRuntime
    from sqlalchemy.orm import sessionmaker

    from tests.test_automatic_research_pipeline import _AssessmentGenerator

    class Extractor:
        def extract(self, *, raw_input, source_url):
            return EventExtraction(
                event_title="Example Corp material",
                company_name="Example Corp",
                ticker=None,
                event_at=None,
                market_reaction=None,
                summary=None,
                research_question="What does the material establish?",
                candidate_factors=("Revenue", "Margin", "Orders"),
                input_kind="material",
            )

    class ClaimsClient:
        model_version = "gateway-material-fixture-v1"

        def chat_json(self, messages, schema_hint=""):
            spans = json.loads(messages[-1]["content"])["spans"]
            return {
                "statements": [
                    {
                        "span_id": span["span_id"],
                        "kind": "reported_claim",
                        "quote": span["verbatim_text"],
                        "quote_start": 0,
                        "quote_end": len(span["verbatim_text"]),
                        "normalized_text": span["verbatim_text"],
                        "assertion_actor": "Example Corp",
                        "subject": "Example Corp",
                        "predicate": "Revenue",
                        "object_text": "100 USD",
                        "numeric_value": "100",
                        "unit": "USD",
                        "observed_period": "2026-08-12",
                        "scope": {"company": "Example Corp", "metric": "Revenue"},
                    }
                    for span in spans
                ]
            }

    receipt = receipt or ResearchGateway(
        cmd_session, AutomaticResearchRuntime(cmd_session, extractor=Extractor())
    ).send_message(
        ResearchActor("team-a", frozenset(), "alice"),
        conversation_id=None,
        text="Example Corp 2026-08-12 Revenue was 100 USD.",
        idempotency_key="frozen-material",
    )
    spec = cmd_session.get(ResearchRunSpec, receipt.run_spec_id)
    run = cmd_session.get(ResearchRun, receipt.native_run_id)
    pipeline = AutomaticResearchPipeline(
        cmd_session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    cmd_session.commit()
    factory = sessionmaker(bind=cmd_session.get_bind(), expire_on_commit=False)
    runner = AcquisitionRunner(factory, adapters={}, llm_client=ClaimsClient())
    for index in range(3):
        with factory() as worker:
            claim = AcquisitionRepository(worker).claim_next(
                worker_id=f"system:acquisition-worker@gateway-test#material-{index}",
                lease_for=timedelta(minutes=5),
            )
            assert claim is not None
            worker.commit()
        runner.run_claim(claim)
    cmd_session.expire_all()
    assert pipeline.advance(run) == "waiting_for_sources"
    for job in cmd_session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        if job.request_snapshot["acquisition_kind"] == "external_gap":
            job.status = job.stage = "failed"
    assert pipeline.advance(run) == "completed"
    cmd_session.commit()
    links = list(
        cmd_session.scalars(
            select(EvidenceLink).where(
                EvidenceLink.review_state == "automatically_admitted"
            )
        )
    )
    assert len(links) == 1
    assert _evidence_document(cmd_session, links[0]).published_at is None
    refs = native_artifacts(cmd_session, spec)
    assert links[0].id in {ref.id for ref in refs if ref.kind == "evidence_link"}
    assert any(ref.kind == "draft" for ref in refs)


def test_revoked_display_rights_withhold_dependent_draft(cmd_session):
    from app.models.source_governance import SourceContract
    from sqlalchemy import text

    spec, _, links = complete_authorized_evidence(cmd_session)
    document = _evidence_document(cmd_session, links[0])
    contract = cmd_session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document.id)
    )
    cmd_session.execute(
        text("UPDATE source_contracts SET allow_display = :allowed WHERE id = :id"),
        {"allowed": False, "id": contract.id.hex},
    )
    cmd_session.commit()
    refs = native_artifacts(cmd_session, spec)
    assert links[0].id not in {ref.id for ref in refs}
    assert not any(ref.kind == "draft" for ref in refs)
