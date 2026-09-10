import hashlib
import json
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.scripts.audit_gildata_ai_walkthrough import (
    MAX_SUMMARY_BYTES,
    WalkthroughAuditFacts,
    WalkthroughAuditInputError,
    _parse_extract_success_summary,
    collect_walkthrough_facts,
    evaluate_walkthrough_facts,
    main,
)
from scripts import walkthrough_cambricon_case as walkthrough


def _accepted_facts() -> WalkthroughAuditFacts:
    return WalkthroughAuditFacts(
        gildata_rounds=2,
        pending_extractions=0,
        summary_candidate_count=8,
        persisted_candidate_count=8,
        gildata_supersession_count=0,
        duplicate_full_body_span_count=0,
        invalid_full_body_span_count=0,
        gildata_document_count=4,
        gildata_contract_count=4,
        gildata_provider_record_count=4,
        published_gildata_evidence_count=1,
        llm_accepted_item_count=1,
        nonempty_gildata_evidence_assessment_count=1,
        unresolved_summary_issue_count=0,
        replay_created_count=0,
        replay_reused_count=4,
        quote_identity_verified_rounds=2,
        run_scope_valid=True,
    )


def test_original_eleven_fact_constructor_remains_accepted():
    facts = WalkthroughAuditFacts(
        gildata_rounds=2,
        pending_extractions=0,
        summary_candidate_count=8,
        persisted_candidate_count=8,
        gildata_supersession_count=0,
        duplicate_full_body_span_count=0,
        invalid_full_body_span_count=0,
        gildata_document_count=4,
        gildata_contract_count=4,
        gildata_provider_record_count=4,
        published_gildata_evidence_count=1,
    )

    assert evaluate_walkthrough_facts(facts).ok is True


def test_extract_success_audit_accepts_and_bounds_repaired_offset_count():
    summary = (
        "candidate extraction completed; unique candidates returned 2; "
        "rule-based accepted items 0; llm returned 3, accepted 2, rejected 1, "
        "offsets repaired 1, llm attempts 2, malformed retries 1; source spans 1"
    )

    counts = _parse_extract_success_summary(summary, expected_span_count=1)

    assert counts is not None
    assert counts.repaired == 1
    assert counts.attempts == 2
    assert counts.malformed_retries == 1
    assert _parse_extract_success_summary(
        summary.replace("offsets repaired 1", "offsets repaired 3"),
        expected_span_count=1,
    ) is None
    assert _parse_extract_success_summary(
        summary.replace("malformed retries 1", "malformed retries 2"),
        expected_span_count=1,
    ) is None


def test_extract_success_audit_accepts_legacy_and_zero_attempt_table_formats():
    legacy = (
        "candidate extraction completed; unique candidates returned 1; "
        "rule-based accepted items 0; llm returned 1, accepted 1, rejected 0; "
        "source spans 1"
    )
    legacy_with_offsets = legacy.replace(
        "rejected 0;", "rejected 0, offsets repaired 0;"
    )
    table_only = (
        "candidate extraction completed; unique candidates returned 2; "
        "rule-based accepted items 2; llm returned 0, accepted 0, rejected 0, "
        "offsets repaired 0, llm attempts 0, malformed retries 0; source spans 1"
    )

    legacy_counts = _parse_extract_success_summary(
        legacy, expected_span_count=1
    )
    legacy_offset_counts = _parse_extract_success_summary(
        legacy_with_offsets, expected_span_count=1
    )
    table_counts = _parse_extract_success_summary(
        table_only, expected_span_count=1
    )

    assert legacy_counts is not None
    assert legacy_counts.attempts is None
    assert legacy_counts.malformed_retries is None
    assert legacy_offset_counts is not None
    assert legacy_offset_counts.attempts is None
    assert table_counts is not None
    assert table_counts.attempts == 0
    assert table_counts.malformed_retries == 0
    assert _parse_extract_success_summary(
        legacy_with_offsets,
        expected_span_count=1,
        prompt_version="extract-v3",
    ) is not None
    assert _parse_extract_success_summary(
        legacy_with_offsets,
        expected_span_count=1,
        prompt_version="extract-v4",
    ) is None
    assert _parse_extract_success_summary(
        table_only,
        expected_span_count=1,
        prompt_version="extract-v4",
    ) is not None
    assert _parse_extract_success_summary(
        table_only,
        expected_span_count=1,
        prompt_version="extract-v5",
    ) is not None
    assert _parse_extract_success_summary(
        legacy_with_offsets,
        expected_span_count=1,
        prompt_version="extract-v5",
    ) is None
    assert _parse_extract_success_summary(
        table_only,
        expected_span_count=1,
        prompt_version="extract-v3",
    ) is None
    assert _parse_extract_success_summary(
        legacy_with_offsets,
        expected_span_count=1,
        prompt_version="extract-v2",
    ) is None


@pytest.mark.parametrize(
    "diagnostics",
    [
        "llm attempts 2",
        "malformed retries 1",
        "offsets repaired 0, llm attempts 0, malformed retries 1",
        "offsets repaired 0, llm attempts 1, malformed retries 1",
    ],
)
def test_extract_success_audit_rejects_partial_or_impossible_retry_diagnostics(
    diagnostics,
):
    summary = (
        "candidate extraction completed; unique candidates returned 0; "
        "rule-based accepted items 0; llm returned 0, accepted 0, rejected 0, "
        f"{diagnostics}; source spans 1"
    )

    assert _parse_extract_success_summary(summary, expected_span_count=1) is None


def test_extract_success_audit_rejects_zero_attempts_with_returned_items():
    summary = (
        "candidate extraction completed; unique candidates returned 1; "
        "rule-based accepted items 0; llm returned 1, accepted 1, rejected 0, "
        "offsets repaired 0, llm attempts 0, malformed retries 0; source spans 1"
    )

    assert _parse_extract_success_summary(summary, expected_span_count=1) is None


def test_extract_success_audit_rejects_zero_attempts_without_rule_coverage():
    summary = (
        "candidate extraction completed; unique candidates returned 0; "
        "rule-based accepted items 0; llm returned 0, accepted 0, rejected 0, "
        "offsets repaired 0, llm attempts 0, malformed retries 0; source spans 1"
    )

    assert _parse_extract_success_summary(summary, expected_span_count=1) is None


def test_extract_success_audit_rejects_nonzero_admission_with_zero_unique():
    summary = (
        "candidate extraction completed; unique candidates returned 0; "
        "rule-based accepted items 1; llm returned 0, accepted 0, rejected 0, "
        "offsets repaired 0, llm attempts 0, malformed retries 0; source spans 1"
    )

    assert _parse_extract_success_summary(summary, expected_span_count=1) is None


def test_audit_accepts_consistent_two_round_live_state():
    result = evaluate_walkthrough_facts(_accepted_facts())

    assert result.ok is True
    assert result.issue_codes == ()
    assert result.metrics["gildata_rounds"] == 2
    assert result.metrics["pending_extractions"] == 0
    assert result.metrics["candidate_count"] == 8
    assert result.metrics["published_evidence_links"] == 1
    assert result.metrics["llm_accepted_items"] == 1
    assert result.metrics["replay_created"] == 0
    assert result.metrics["replay_reused"] == 4


@pytest.mark.parametrize(
    ("field", "value", "issue_code"),
    [
        ("gildata_rounds", 1, "two_ingest_rounds"),
        ("pending_extractions", 1, "no_pending_extractions"),
        ("persisted_candidate_count", 7, "candidate_totals_match"),
        ("gildata_supersession_count", 1, "no_false_supersession"),
        ("duplicate_full_body_span_count", 1, "duplicate_full_body_span"),
        ("invalid_full_body_span_count", 1, "full_body_span_integrity"),
        ("gildata_document_count", 0, "gildata_documents_present"),
        ("gildata_contract_count", 3, "one_contract_per_document"),
        (
            "gildata_provider_record_count",
            3,
            "one_provider_record_per_document",
        ),
        ("published_gildata_evidence_count", 0, "formal_evidence_published"),
        ("llm_accepted_item_count", 0, "llm_candidate_accepted"),
        (
            "nonempty_gildata_evidence_assessment_count",
            0,
            "nonempty_evidence_assessment",
        ),
        ("unresolved_summary_issue_count", 1, "no_unresolved_summary_issues"),
        ("replay_created_count", 1, "ingest_replay_created_nothing"),
        ("replay_reused_count", 0, "ingest_replay_reused_records"),
        ("quote_identity_verified_rounds", 1, "quote_identity_verified"),
        (
            "per_document_candidate_mismatch_count",
            1,
            "per_document_candidate_totals_match",
        ),
        (
            "invalid_extract_run_scope_count",
            1,
            "extract_runs_match_document_spans",
        ),
        ("run_scope_valid", False, "run_scope_valid"),
    ],
)
def test_audit_fails_closed_for_each_acceptance_gate(field, value, issue_code):
    result = evaluate_walkthrough_facts(replace(_accepted_facts(), **{field: value}))

    assert result.ok is False
    assert issue_code in result.issue_codes
    assert all(isinstance(metric, int) for metric in result.metrics.values())


def _replay_round(case_id: uuid.UUID, round_number: int) -> dict[str, object]:
    return {
        "round": round_number,
        "http_status": 201,
        "case_id": str(case_id),
        "research_reports": 0,
        "research_reports_reused": 1,
        "announcements": 0,
        "announcements_reused": 0,
        "news": 0,
        "news_reused": 0,
        "macro_series": 0,
        "macro_series_reused": 0,
        "spans": 0,
        "spans_reused": 1,
        "valuations_written": 0,
        "valuations_skipped": 3,
        "quote_identity_verified": True,
    }


def _seed_accepted_walkthrough(
    session,
    *,
    link_review_state: str = "reviewed",
    assessment_uses_evidence: bool = True,
    llm_counts: tuple[int, int, int] = (1, 1, 0),
    invalid_contract_scope: bool = False,
    invalid_run_span_scope: bool = False,
    document_id: uuid.UUID | None = None,
) -> tuple[dict, dict[str, object]]:
    from app.models.ledger import (
        AIAssessment,
        AIRun,
        AtomicClaimCandidate,
        AtomicClaimReview,
        CaseDocumentVersion,
        CaseTenantAdmission,
        DocumentVersion,
        EvidenceLink,
        EvidenceSnapshot,
        ResearchCase,
        SourceSpan,
        SourceStatement,
        Thesis,
    )
    from app.models.proposals import Proposal, ProposalReviewDecision
    from app.models.source_governance import ProviderRecord, SourceContract
    from app.models.versions import EvidenceLinkVersion

    now = datetime.now(UTC)
    case_id = uuid.uuid4()
    initial_id = uuid.uuid4()
    document_id = document_id or uuid.uuid4()
    span_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    statement_id = uuid.uuid4()
    thesis_id = uuid.uuid4()
    atomic_review_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    link_id = uuid.uuid4()

    body = (
        "寒武纪公开资料显示收入持续增长并已实现盈利，相关数据来自正式研究报告正文。"
        "报告同时说明研发投入、客户需求和产品交付情况，足以支持独立事实抽取与人工复核。"
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    source_url = f"gildata://research-report/{digest}"
    query_sha256 = "a" * 64
    request_scope = {
        "source_kind": "research-report",
        "query_sha256": query_sha256,
    }
    contract_version = "gildata-local-rights-v1"
    intake_metadata = {
        "research_source_type": "licensed_provider",
        "provider_name": "gildata",
        "provider_record_id": f"research-report:{digest}",
        "retrieval_reference": source_url,
        "request_scope": [] if invalid_contract_scope else request_scope,
        "permissions": {
            "ai_processing": True,
            "display": True,
            "export": False,
            "api": False,
        },
        "contract_version": contract_version,
        "downstream_restrictions": ["仅限当前 Case 研究与人工审核"],
        "_source_contract_declared_url": source_url,
        "_source_contract_declared_url_is_explicit": True,
    }

    case = ResearchCase(
        id=case_id,
        title="walkthrough",
        industry_topic="ai_compute",
        created_at=now,
        created_by="walkthrough-reviewer",
    )
    initial = DocumentVersion(
        id=initial_id,
        content_sha256="0" * 64,
        source_url="event://pasted-news",
        available_at=now,
        acquired_at=now,
        parser_version="pasted-text-1",
    )
    initial_span = SourceSpan(
        document_version_id=initial_id,
        locator={"kind": "pasted"},
        verbatim_text="简短初始材料",
        text_sha256=hashlib.sha256("简短初始材料".encode()).hexdigest(),
    )
    document = DocumentVersion(
        id=document_id,
        content_sha256=digest,
        source_url=source_url,
        natural_key=f"gildata:{digest[:23]}",
        published_at=now,
        available_at=now,
        acquired_at=now,
        parser_version="gildata-mcp-1",
        title="正式研究报告",
        byte_size=len(body.encode("utf-8")),
        parse_state="success",
        source_authority="licensed_research",
    )
    span = SourceSpan(
        id=span_id,
        document_version_id=document_id,
        locator={
            "kind": "research_report",
            "page": 1,
            "paragraph": 1,
            "parser": "gildata-mcp-1",
            "case_id": str(case_id),
        },
        verbatim_text=body,
        text_sha256=digest,
    )
    candidate = AtomicClaimCandidate(
        id=candidate_id,
        source_span_id=span_id,
        canonical_key="b" * 64,
        quote=body[:12],
        quote_start=0,
        quote_end=12,
        quote_sha256=hashlib.sha256(body[:12].encode("utf-8")).hexdigest(),
        normalized_text="寒武纪收入增长并实现盈利",
        claim_type="research_opinion",
        authority_level="licensed_research",
        structured_fields={"run_ref": f"extract:{uuid.uuid4()}"},
        validation_result={"quote_continuous": True},
        created_at=now,
    )
    statement = SourceStatement(
        id=statement_id,
        source_span_id=span_id,
        atomic_claim_candidate_id=candidate_id,
        kind="research_opinion",
        normalized_text="寒武纪收入增长并实现盈利",
        created_at=now,
    )
    thesis = Thesis(
        id=thesis_id,
        research_case_id=case_id,
        statement="寒武纪盈利拐点成立",
        created_at=now,
        created_by="walkthrough-reviewer",
    )
    atomic_review = AtomicClaimReview(
        id=atomic_review_id,
        atomic_claim_candidate_id=candidate_id,
        outcome="confirmed",
        reviewer="walkthrough-reviewer",
        reason="confirmed",
        idempotency_key="audit-atomic-review",
        published_source_statement_id=statement_id,
        created_at=now,
    )
    llm_returned, llm_accepted, llm_rejected = llm_counts
    unique_returned = int(llm_accepted > 0)
    run = AIRun(
        id=uuid.uuid4(),
        kind="extract",
        model_version="test-model",
        prompt_version="extract-v1",
        input_ref={
            "document_version_id": str(document_id),
            "span_ids": [str(uuid.uuid4()) if invalid_run_span_scope else str(span_id)],
        },
        output_summary=(
            "candidate extraction completed; "
            f"unique candidates returned {unique_returned}; "
            "rule-based accepted items 0; "
            f"llm returned {llm_returned}, accepted {llm_accepted}, "
            f"rejected {llm_rejected}; "
            "source spans 1"
        ),
        status="success",
        started_at=now,
        finished_at=now,
    )
    proposal = Proposal(
        id=proposal_id,
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement_id),
            "role": "supports",
            "reason": "confirmed",
            "scope": {"period": "current"},
        },
        target_context={"thesis_id": str(thesis_id)},
        proposed_by_type="ai",
        proposed_by_ref="test-model",
        proposed_at=now,
        input_entity_ids=[str(statement_id)],
        version=1,
        status="decided",
        decided_at=now,
        research_case_id=case_id,
    )
    decision = ProposalReviewDecision(
        id=decision_id,
        proposal_id=proposal_id,
        outcome="confirmed",
        reason="confirmed",
        reviewer_id="walkthrough-reviewer",
        decided_at=now,
        expected_proposal_version=1,
    )
    link = EvidenceLink(
        id=link_id,
        thesis_id=thesis_id,
        source_statement_id=statement_id,
        role="supports",
        reason="confirmed",
        scope={"period": "current"},
        available_at=now,
        creator_type="human",
        review_state=link_review_state,
        model_version="test-model",
        created_at=now,
    )
    version = EvidenceLinkVersion(
        id=uuid.uuid4(),
        evidence_link_id=link_id,
        version=1,
        thesis_id=thesis_id,
        source_statement_id=statement_id,
        role="supports",
        reason="confirmed",
        scope={"period": "current"},
        available_at=now,
        proposal_id=proposal_id,
        review_decision_id=decision_id,
        model_version="test-model",
        created_at=now,
    )
    snapshot = EvidenceSnapshot(
        id=uuid.uuid4(),
        thesis_id=thesis_id,
        cutoff=now,
        evidence_link_ids=[str(link_id)] if assessment_uses_evidence else [],
        created_at=now,
    )
    assessment = AIAssessment(
        id=uuid.uuid4(),
        snapshot_id=snapshot.id,
        conclusion="supported",
        rationale="confirmed",
        gaps=[],
        displayed_as_provisional=True,
        creator_type="ai",
        model_version="test-model",
        created_at=now,
    )
    session.add_all(
        [
            case,
            initial,
            initial_span,
            document,
            CaseDocumentVersion(
                research_case_id=case_id,
                document_version_id=initial_id,
                linked_at=now,
            ),
            CaseDocumentVersion(
                research_case_id=case_id,
                document_version_id=document_id,
                linked_at=now,
            ),
            CaseTenantAdmission(
                research_case_id=case_id,
                tenant_id="test-team",
                initial_document_version_id=initial_id,
                admitted_by="walkthrough-reviewer",
                admitted_at=now,
            ),
            span,
            candidate,
            statement,
            thesis,
            atomic_review,
            run,
            SourceContract(
                document_version_id=document_id,
                source_type="licensed_provider",
                research_source_type="licensed_provider",
                provider_or_tenant="gildata",
                allow_ai_processing=True,
                allow_display=True,
                allow_export=False,
                allow_api=False,
                region="not_recorded",
                retention_policy="case_retained",
                deletion_policy="not_recorded",
                downstream_restrictions=["仅限当前 Case 研究与人工审核"],
                contract_version=contract_version,
                intake_metadata=intake_metadata,
                declared_by="tenant:test-team",
                created_at=now,
            ),
            ProviderRecord(
                document_version_id=document_id,
                provider_name="gildata",
                provider_record_id=f"research-report:{digest}",
                request_scope=request_scope,
                retrieval_reference=source_url,
                content_sha256=digest,
                retrieved_at=now,
                contract_version=contract_version,
                created_at=now,
            ),
            proposal,
            decision,
            link,
            version,
            snapshot,
            assessment,
        ]
    )
    session.flush()

    summary = {
        "run_id": "audit-test",
        "phases": {
            "P1_create_case": {"case_id": str(case_id)},
            "P2_ingest": [
                _replay_round(case_id, 1),
                _replay_round(case_id, 2),
            ],
            "P3_extract": {
                "candidates": 1,
                "per_document": [
                    {
                        "version_id": str(document_id),
                        "candidates": 1,
                        "claim_types": {"research_opinion": 1},
                        "reason": None,
                    }
                ],
            },
        },
        "issues": [],
    }
    entities = {
        "case": case,
        "initial": initial,
        "document": document,
        "span": span,
        "candidate": candidate,
        "statement": statement,
        "run": run,
        "link": link,
        "assessment": assessment,
    }
    return summary, entities


def _add_scoped_gildata_document(
    session,
    *,
    case_id: uuid.UUID,
    source_kind: str = "research-report",
    body: str | None = None,
    with_span: bool = True,
):
    from app.models.ledger import CaseDocumentVersion, DocumentVersion, SourceSpan

    body = body or (
        "这是一份额外的受治理资料正文，用于验证审计不会遗漏仍待提取的有效文档。"
        "正文包含足够多的有意义字符，能够通过内容质量门槛并进入提取队列。"
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    document = DocumentVersion(
        content_sha256=digest,
        source_url=f"gildata://{source_kind}/{digest}",
        natural_key=f"gildata:{digest[:23]}",
        published_at=now,
        available_at=now,
        acquired_at=now,
        parser_version="gildata-mcp-1",
        title="额外资料",
        byte_size=len(body.encode("utf-8")),
        parse_state="success",
        source_authority="licensed_research",
    )
    session.add(document)
    session.flush()
    session.add(
        CaseDocumentVersion(
            research_case_id=case_id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
    if with_span:
        locator_kind = {
            "research-report": "research_report",
            "announcement": "announcement",
            "news": "news",
            "macro-industry": "macro_series",
        }[source_kind]
        session.add(
            SourceSpan(
                document_version_id=document.id,
                locator={"kind": locator_kind, "parser": "gildata-mcp-1"},
                verbatim_text=body,
                text_sha256=digest,
            )
        )
    session.flush()
    return document


def test_collect_walkthrough_facts_reconciles_the_scoped_live_run(session):
    summary, _ = _seed_accepted_walkthrough(session)

    facts = collect_walkthrough_facts(session, summary)

    assert facts == _accepted_facts().__class__(
        gildata_rounds=2,
        pending_extractions=0,
        summary_candidate_count=1,
        persisted_candidate_count=1,
        gildata_supersession_count=0,
        duplicate_full_body_span_count=0,
        invalid_full_body_span_count=0,
        gildata_document_count=1,
        gildata_contract_count=1,
        gildata_provider_record_count=1,
        published_gildata_evidence_count=1,
        llm_accepted_item_count=1,
        nonempty_gildata_evidence_assessment_count=1,
        unresolved_summary_issue_count=0,
        replay_created_count=0,
        replay_reused_count=4,
        quote_identity_verified_rounds=2,
        run_scope_valid=True,
    )
    assert not session.new
    assert not session.dirty
    assert not session.deleted


def test_collect_excludes_non_gildata_candidates_inside_the_case(session):
    from app.models.ledger import AtomicClaimCandidate, SourceSpan

    summary, entities = _seed_accepted_walkthrough(session)
    initial = entities["initial"]
    span = SourceSpan(
        document_version_id=initial.id,
        locator={"kind": "pasted"},
        verbatim_text="非 Gildata 正文",
        text_sha256=hashlib.sha256("非 Gildata 正文".encode()).hexdigest(),
    )
    session.add(span)
    session.flush()
    session.add(
        AtomicClaimCandidate(
            source_span_id=span.id,
            canonical_key="c" * 64,
            quote="非 Gildata",
            quote_start=0,
            quote_end=10,
            quote_sha256=hashlib.sha256("非 Gildata".encode()).hexdigest(),
            normalized_text="非 Gildata 候选",
            claim_type="research_opinion",
            authority_level="unknown",
            structured_fields={"run_ref": "extract:foreign"},
            validation_result={"quote_continuous": True},
            created_at=datetime.now(UTC),
        )
    )
    summary["phases"]["P3_extract"]["candidates"] = 2
    summary["phases"]["P3_extract"]["per_document"].append(
        {"version_id": str(initial.id), "candidates": 1}
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.summary_candidate_count == 1
    assert facts.persisted_candidate_count == 1
    assert facts.run_scope_valid is True


def test_collect_marks_an_extra_case_as_cross_run_scope(session):
    from app.models.ledger import ResearchCase

    summary, _ = _seed_accepted_walkthrough(session)
    session.add(
        ResearchCase(
            title="other run",
            industry_topic="other",
            created_at=datetime.now(UTC),
            created_by="other",
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.persisted_candidate_count == 1
    assert facts.run_scope_valid is False


def test_collect_rejects_control_prefixed_gildata_scheme_as_pollution(session):
    from app.models.ledger import CaseDocumentVersion, DocumentVersion

    summary, entities = _seed_accepted_walkthrough(session)
    case_id = entities["case"].id
    now = datetime.now(UTC)
    document = DocumentVersion(
        content_sha256="d" * 64,
        source_url=f"\x85  GiLdAtA://research-report/{'d' * 64}",
        natural_key=f"gildata:{'d' * 23}",
        available_at=now,
        acquired_at=now,
        parser_version="generic-parser",
        parse_state="success",
        source_authority="licensed_research",
    )
    session.add(document)
    session.flush()
    session.add(
        CaseDocumentVersion(
            research_case_id=case_id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.gildata_document_count == 1
    assert facts.run_scope_valid is False


def test_collect_requires_every_eligible_gildata_document_in_p3_summary(session):
    summary, entities = _seed_accepted_walkthrough(session)
    _add_scoped_gildata_document(session, case_id=entities["case"].id)

    facts = collect_walkthrough_facts(session, summary)

    assert facts.pending_extractions == 1
    assert facts.run_scope_valid is False


def test_collect_requires_every_eligible_case_document_to_finish_extraction(session):
    from app.models.ledger import SourceSpan

    summary, entities = _seed_accepted_walkthrough(session)
    body = (
        "这是研究 Case 的初始公开事件材料，正文长度足以进入事实提取流程。"
        "即使它不是 Gildata 文档，终态验收也不得遗漏仍未执行的提取任务。"
    )
    session.add(
        SourceSpan(
            document_version_id=entities["initial"].id,
            locator={"kind": "pasted"},
            verbatim_text=body,
            text_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.pending_extractions == 1
    assert facts.run_scope_valid is False


def test_collect_requires_a_trusted_latest_run_for_every_eligible_document(session):
    from app.models.ledger import (
        AtomicClaimCandidate,
        SourceSpan,
        SourceStatement,
    )

    summary, entities = _seed_accepted_walkthrough(session)
    body = (
        "这是研究 Case 的另一份有效公开材料，正文长度足以进入事实提取流程。"
        "它已有候选和正式陈述，但审计仍必须要求对应的可信提取运行。"
    )
    span = SourceSpan(
        document_version_id=entities["initial"].id,
        locator={"kind": "pasted"},
        verbatim_text=body,
        text_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
    session.add(span)
    session.flush()
    quote = body[:12]
    candidate = AtomicClaimCandidate(
        source_span_id=span.id,
        canonical_key="e" * 64,
        quote=quote,
        quote_start=0,
        quote_end=len(quote),
        quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        normalized_text="有效材料已有陈述",
        claim_type="reported_claim",
        authority_level="user_supplied",
        structured_fields={"run_ref": "extract:missing-run"},
        validation_result={"quote_continuous": True},
        created_at=datetime.now(UTC),
    )
    session.add(candidate)
    session.flush()
    session.add(
        SourceStatement(
            source_span_id=span.id,
            atomic_claim_candidate_id=candidate.id,
            kind="reported_claim",
            normalized_text="有效材料已有陈述",
            created_at=datetime.now(UTC),
        )
    )
    summary["phases"]["P3_extract"]["candidates"] = 2
    summary["phases"]["P3_extract"]["per_document"].append(
        {"version_id": str(entities["initial"].id), "candidates": 1}
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)
    result = evaluate_walkthrough_facts(facts)

    assert facts.pending_extractions == 0
    assert facts.invalid_extract_run_scope_count == 1
    assert "extract_runs_match_document_spans" in result.issue_codes


def test_collect_counts_missing_and_duplicate_full_body_spans(session):
    from app.models.ledger import SourceSpan

    summary, entities = _seed_accepted_walkthrough(session)
    document = entities["document"]
    original_span = entities["span"]
    session.add(
        SourceSpan(
            document_version_id=document.id,
            locator=dict(original_span.locator),
            verbatim_text=original_span.verbatim_text,
            text_sha256=original_span.text_sha256,
        )
    )
    missing = _add_scoped_gildata_document(
        session,
        case_id=entities["case"].id,
        source_kind="announcement",
        with_span=False,
    )
    summary["phases"]["P3_extract"]["per_document"].append(
        {"version_id": str(missing.id), "candidates": 0}
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.duplicate_full_body_span_count == 1
    assert facts.invalid_full_body_span_count == 1


def test_collect_exempts_macro_metric_spans_from_full_body_invariant(session):
    from app.models.ledger import SourceSpan

    summary, entities = _seed_accepted_walkthrough(session)
    macro = _add_scoped_gildata_document(
        session,
        case_id=entities["case"].id,
        source_kind="macro-industry",
    )
    session.add(
        SourceSpan(
            document_version_id=macro.id,
            locator={"kind": "macro_series", "parser": "gildata-mcp-1"},
            verbatim_text="第二个确定性指标窗口",
            text_sha256=hashlib.sha256("第二个确定性指标窗口".encode()).hexdigest(),
        )
    )
    summary["phases"]["P3_extract"]["per_document"].append(
        {"version_id": str(macro.id), "candidates": 0}
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.duplicate_full_body_span_count == 0
    assert facts.invalid_full_body_span_count == 0


def test_collect_requires_formal_review_chain_and_assessment_input(session):
    unreviewed_summary, _ = _seed_accepted_walkthrough(
        session,
        link_review_state="machine_generated",
    )

    unreviewed = collect_walkthrough_facts(session, unreviewed_summary)

    assert unreviewed.published_gildata_evidence_count == 0
    assert unreviewed.nonempty_gildata_evidence_assessment_count == 0


def test_collect_requires_assessment_to_capture_formal_gildata_evidence(session):
    summary, _ = _seed_accepted_walkthrough(
        session,
        assessment_uses_evidence=False,
    )

    facts = collect_walkthrough_facts(session, summary)

    assert facts.published_gildata_evidence_count == 1
    assert facts.nonempty_gildata_evidence_assessment_count == 0


def test_collect_does_not_credit_cross_thesis_evidence_to_an_assessment(session):
    from app.models.ledger import AIAssessment, EvidenceSnapshot, Thesis

    summary, entities = _seed_accepted_walkthrough(
        session,
        assessment_uses_evidence=False,
    )
    now = datetime.now(UTC)
    unrelated_thesis = Thesis(
        research_case_id=entities["case"].id,
        statement="同 Case 的另一条命题",
        created_at=now,
        created_by="walkthrough-reviewer",
    )
    session.add(unrelated_thesis)
    session.flush()
    snapshot = EvidenceSnapshot(
        thesis_id=unrelated_thesis.id,
        cutoff=now,
        evidence_link_ids=[str(entities["link"].id)],
        created_at=now,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        AIAssessment(
            snapshot_id=snapshot.id,
            conclusion="supported",
            rationale="unrelated",
            gaps=[],
            displayed_as_provisional=True,
            creator_type="ai",
            model_version="test-model",
            created_at=now,
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)
    result = evaluate_walkthrough_facts(facts)

    assert facts.published_gildata_evidence_count == 1
    assert facts.nonempty_gildata_evidence_assessment_count == 0
    assert "nonempty_evidence_assessment" in result.issue_codes


def test_collect_does_not_credit_a_mixed_thesis_evidence_snapshot(session):
    from app.models.ledger import AIAssessment, EvidenceLink, EvidenceSnapshot, Thesis

    summary, entities = _seed_accepted_walkthrough(
        session,
        assessment_uses_evidence=False,
    )
    now = datetime.now(UTC)
    unrelated_thesis = Thesis(
        research_case_id=entities["case"].id,
        statement="同 Case 的另一条命题",
        created_at=now,
        created_by="walkthrough-reviewer",
    )
    session.add(unrelated_thesis)
    session.flush()
    cross_thesis_link = EvidenceLink(
        thesis_id=unrelated_thesis.id,
        source_statement_id=entities["statement"].id,
        role="supports",
        reason="cross thesis",
        scope={},
        available_at=now,
        creator_type="human",
        review_state="reviewed",
        created_at=now,
    )
    session.add(cross_thesis_link)
    session.flush()
    snapshot = EvidenceSnapshot(
        thesis_id=entities["link"].thesis_id,
        cutoff=now,
        evidence_link_ids=[
            str(entities["link"].id),
            str(cross_thesis_link.id),
        ],
        created_at=now,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        AIAssessment(
            snapshot_id=snapshot.id,
            conclusion="supported",
            rationale="mixed evidence",
            gaps=[],
            displayed_as_provisional=True,
            creator_type="ai",
            model_version="test-model",
            created_at=now,
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.published_gildata_evidence_count == 1
    assert facts.nonempty_gildata_evidence_assessment_count == 0


def test_collect_parses_conserved_llm_acceptance_counts(session):
    summary, _ = _seed_accepted_walkthrough(
        session,
        llm_counts=(2, 0, 2),
    )

    facts = collect_walkthrough_facts(session, summary)

    assert facts.llm_accepted_item_count == 0
    assert facts.run_scope_valid is True


def test_collect_rejects_nonconserved_llm_audit_counts(session):
    summary, _ = _seed_accepted_walkthrough(
        session,
        llm_counts=(1, 1, 1),
    )

    facts = collect_walkthrough_facts(session, summary)

    assert facts.llm_accepted_item_count == 0
    assert facts.run_scope_valid is False


def test_collect_reconciles_candidate_counts_for_each_gildata_document(session):
    from app.models.ledger import AIRun, AtomicClaimCandidate, SourceSpan

    summary, entities = _seed_accepted_walkthrough(session)
    second_document = _add_scoped_gildata_document(
        session,
        case_id=entities["case"].id,
        source_kind="news",
    )
    second_span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == second_document.id)
    )
    assert second_span is not None
    quote = second_span.verbatim_text[:12]
    for key in ("c" * 64, "d" * 64):
        session.add(
            AtomicClaimCandidate(
                source_span_id=second_span.id,
                canonical_key=key,
                quote=quote,
                quote_start=0,
                quote_end=len(quote),
                quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                normalized_text="额外正式资料候选",
                claim_type="reported_claim",
                authority_level="licensed_research",
                structured_fields={"run_ref": "extract:prior"},
                validation_result={"quote_continuous": True},
                created_at=datetime.now(UTC),
            )
        )
    session.add(
        AIRun(
            kind="extract",
            model_version="test-model",
            prompt_version="extract-v1",
            input_ref={
                "document_version_id": str(second_document.id),
                "span_ids": [str(second_span.id)],
            },
            output_summary=(
                "candidate extraction completed; unique candidates returned 2; "
                "rule-based accepted items 2; "
                "llm returned 0, accepted 0, rejected 0; source spans 1"
            ),
            status="success",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
    )
    summary["phases"]["P3_extract"] = {
        "candidates": 3,
        "per_document": [
            {
                "version_id": str(entities["document"].id),
                "candidates": 2,
            },
            {"version_id": str(second_document.id), "candidates": 1},
        ],
    }
    session.flush()

    facts = collect_walkthrough_facts(session, summary)
    result = evaluate_walkthrough_facts(facts)

    assert facts.summary_candidate_count == 3
    assert facts.persisted_candidate_count == 3
    assert facts.per_document_candidate_mismatch_count == 2
    assert "per_document_candidate_totals_match" in result.issue_codes


def test_collect_rejects_extract_run_not_bound_to_document_spans(session):
    summary, _ = _seed_accepted_walkthrough(
        session,
        invalid_run_span_scope=True,
    )

    facts = collect_walkthrough_facts(session, summary)
    result = evaluate_walkthrough_facts(facts)

    assert facts.invalid_extract_run_scope_count == 1
    assert facts.llm_accepted_item_count == 0
    assert "extract_runs_match_document_spans" in result.issue_codes


def test_collect_rejects_ambiguous_latest_extract_run_timestamp(session):
    from app.models.ledger import AIRun

    summary, entities = _seed_accepted_walkthrough(session)
    original = entities["run"]
    session.add(
        AIRun(
            id=uuid.uuid4(),
            kind="extract",
            model_version="other-model",
            prompt_version="extract-v1",
            input_ref=dict(original.input_ref),
            output_summary=(
                "candidate extraction completed; unique candidates returned 1; "
                "rule-based accepted items 0; "
                "llm returned 1, accepted 1, rejected 0; source spans 1"
            ),
            status="success",
            started_at=original.started_at,
            finished_at=original.finished_at,
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.run_scope_valid is False


@pytest.mark.parametrize(
    "document_reference",
    ["uppercase", "malformed", "outside_scope"],
)
def test_collect_fails_closed_for_every_extract_run_document_reference(
    session,
    document_reference,
):
    from app.models.ledger import AIRun

    known_document_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    summary, entities = _seed_accepted_walkthrough(
        session,
        document_id=known_document_id,
    )
    original = entities["run"]
    if document_reference == "uppercase":
        version_id = str(known_document_id).upper()
    elif document_reference == "malformed":
        version_id = "not-a-document-uuid"
    else:
        version_id = str(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
    session.add(
        AIRun(
            id=uuid.uuid4(),
            kind="extract",
            model_version="other-model",
            prompt_version="extract-v1",
            input_ref={
                "document_version_id": version_id,
                "span_ids": [str(entities["span"].id)],
            },
            output_summary=original.output_summary,
            status="success",
            started_at=original.started_at + timedelta(seconds=1),
            finished_at=original.finished_at + timedelta(seconds=1),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)
    result = evaluate_walkthrough_facts(facts)

    assert facts.invalid_extract_run_scope_count == 1
    assert facts.run_scope_valid is False
    assert result.ok is False


def test_collect_ignores_malformed_document_reference_on_non_extract_run(session):
    from app.models.ledger import AIRun

    summary, entities = _seed_accepted_walkthrough(session)
    original = entities["run"]
    session.add(
        AIRun(
            id=uuid.uuid4(),
            kind="assess",
            model_version="other-model",
            prompt_version="assess-v1",
            input_ref={"document_version_id": "not-a-document-uuid"},
            output_summary="assessment completed",
            status="success",
            started_at=original.started_at + timedelta(seconds=1),
            finished_at=original.finished_at + timedelta(seconds=1),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.invalid_extract_run_scope_count == 0
    assert facts.run_scope_valid is True


def test_collect_rejects_success_summary_with_extra_untrusted_prose(session):
    from app.models.ledger import AIRun

    summary, entities = _seed_accepted_walkthrough(session)
    original = entities["run"]
    session.add(
        AIRun(
            id=uuid.uuid4(),
            kind="extract",
            model_version="other-model",
            prompt_version="extract-v1",
            input_ref=dict(original.input_ref),
            output_summary=f"untrusted prefix; {original.output_summary}",
            status="success",
            started_at=original.started_at + timedelta(seconds=1),
            finished_at=original.finished_at + timedelta(seconds=1),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.llm_accepted_item_count == 0
    assert facts.run_scope_valid is False


@pytest.mark.parametrize(
    "output_summary",
    [
        (
            "candidate extraction completed; unique candidates returned 1; "
            "rule-based accepted items 0; "
            "llm returned 1, accepted 1, rejected 0; source spans 2"
        ),
        (
            "skipped: no source spans attached to this version; "
            "llm returned 0, accepted 0, rejected 0"
        ),
        (
            "candidate extraction completed; unique candidates returned 999; "
            "rule-based accepted items 0; "
            "llm returned 1, accepted 1, rejected 0; source spans 1"
        ),
    ],
)
def test_collect_reconciles_success_summary_with_actual_span_count(
    session,
    output_summary,
):
    from app.models.ledger import AIRun

    summary, entities = _seed_accepted_walkthrough(session)
    original = entities["run"]
    session.add(
        AIRun(
            kind="extract",
            model_version="other-model",
            prompt_version="extract-v1",
            input_ref=dict(original.input_ref),
            output_summary=output_summary,
            status="success",
            started_at=original.started_at + timedelta(seconds=1),
            finished_at=original.finished_at + timedelta(seconds=1),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.invalid_extract_run_scope_count == 1
    assert facts.run_scope_valid is False


def test_collect_rejects_latest_failed_run_even_when_statements_exist(session):
    from app.models.ledger import AIRun

    summary, entities = _seed_accepted_walkthrough(session)
    original = entities["run"]
    session.add(
        AIRun(
            kind="extract",
            model_version="other-model",
            prompt_version="extract-v1",
            input_ref=dict(original.input_ref),
            output_summary="llm attempts 1; failure category provider_unavailable",
            status="failed",
            error="AI operation failed",
            started_at=original.started_at + timedelta(seconds=1),
            finished_at=original.finished_at + timedelta(seconds=1),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.pending_extractions == 0
    assert facts.invalid_extract_run_scope_count == 1
    assert facts.run_scope_valid is False


@pytest.mark.parametrize("started_at_offset", [-1, 1])
def test_collect_rejects_extra_extract_run_input_reference_keys(
    session,
    started_at_offset,
):
    from app.models.ledger import AIRun

    summary, entities = _seed_accepted_walkthrough(session)
    original = entities["run"]
    session.add(
        AIRun(
            kind="extract",
            model_version="other-model",
            prompt_version="extract-v1",
            input_ref={**original.input_ref, "licensed_blob": "provider-body-sentinel"},
            output_summary=original.output_summary,
            status="success",
            started_at=original.started_at + timedelta(seconds=started_at_offset),
            finished_at=original.finished_at + timedelta(seconds=started_at_offset),
        )
    )
    session.flush()

    facts = collect_walkthrough_facts(session, summary)

    assert facts.invalid_extract_run_scope_count == 1
    assert facts.run_scope_valid is False


def test_collect_counts_only_compatible_contract_and_provider_bundles(session):
    summary, _ = _seed_accepted_walkthrough(
        session,
        invalid_contract_scope=True,
    )

    facts = collect_walkthrough_facts(session, summary)

    assert facts.gildata_document_count == 1
    assert facts.gildata_contract_count == 0
    assert facts.gildata_provider_record_count == 0


def test_collect_marks_duplicate_p3_document_ids_as_invalid_run_scope(session):
    summary, entities = _seed_accepted_walkthrough(session)
    duplicate = dict(summary["phases"]["P3_extract"]["per_document"][0])
    summary["phases"]["P3_extract"]["per_document"].append(duplicate)
    summary["phases"]["P3_extract"]["candidates"] = 2

    facts = collect_walkthrough_facts(session, summary)

    assert facts.summary_candidate_count == 2
    assert facts.persisted_candidate_count == 1
    assert facts.run_scope_valid is False
    assert entities["candidate"].id is not None


def test_collect_rejects_boolean_counts_without_echoing_summary_values(session):
    summary, _ = _seed_accepted_walkthrough(session)
    summary["phases"]["P3_extract"]["per_document"][0]["candidates"] = True
    summary["secret-sentinel"] = "licensed body must not escape"

    with pytest.raises(WalkthroughAuditInputError) as exc_info:
        collect_walkthrough_facts(session, summary)

    assert "licensed body must not escape" not in str(exc_info.value)


@pytest.mark.parametrize(
    "location",
    ["root", "facts", "phase", "p2_item", "p3_document", "issue"],
)
def test_collect_rejects_unexpected_summary_prose_fields_without_echoing_them(
    session,
    location,
):
    summary, _ = _seed_accepted_walkthrough(session)
    sentinel = "licensed-provider-body-sentinel"
    if location == "root":
        summary["licensed_blob"] = sentinel
    elif location == "facts":
        summary["facts"] = {"licensed_blob": sentinel}
    elif location == "phase":
        summary["phases"]["P3_extract"]["licensed_blob"] = sentinel
    elif location == "p2_item":
        summary["phases"]["P2_ingest"][0]["licensed_blob"] = sentinel
    elif location == "p3_document":
        summary["phases"]["P3_extract"]["per_document"][0]["licensed_blob"] = sentinel
    else:
        summary["issues"] = [{"licensed_blob": sentinel}]

    with pytest.raises(WalkthroughAuditInputError) as exc_info:
        collect_walkthrough_facts(session, summary)

    assert sentinel not in str(exc_info.value)


@pytest.mark.parametrize(
    "location",
    ["p3_reason", "p3_scalar", "p9_notes", "issue_code", "p0_scalar", "p5_value"],
)
def test_collect_rejects_invalid_values_under_known_summary_keys_without_echo(
    session,
    location,
):
    summary, _ = _seed_accepted_walkthrough(session)
    sentinel = "licensed provider body sentinel"
    if location == "p3_reason":
        summary["phases"]["P3_extract"]["per_document"][0]["reason"] = sentinel
    elif location == "p3_scalar":
        summary["phases"]["P3_extract"]["pending_after"] = sentinel
    elif location == "p9_notes":
        summary["phases"]["P9_enrichment"] = {
            "theme_roles": 0,
            "causal_steps": 0,
            "causal_edges": 0,
            "funds": 0,
            "holding_disclosures": 0,
            "notes": [sentinel],
        }
    elif location == "issue_code":
        summary["issues"] = [{"code": sentinel}]
    elif location == "p0_scalar":
        summary["phases"]["P0_preflight"] = {"fund_holders_rows": sentinel}
    else:
        summary["phases"]["P5_pre_review_assessment_T1"] = {"conclusion": sentinel}

    with pytest.raises(WalkthroughAuditInputError) as exc_info:
        collect_walkthrough_facts(session, summary)

    assert sentinel not in str(exc_info.value)


def test_collect_accepts_the_complete_runner_summary_projection(session):
    summary, entities = _seed_accepted_walkthrough(session)
    case_id = str(entities["case"].id)
    thesis_id = str(uuid.uuid4())
    summary["phases"].update(
        {
            "P0_preflight": {
                "tools": ["FinQuery"],
                "quote_metrics": {"latest_price": "1106.0", "pe_ttm": "255.759"},
                "annual_2025": {"revenue_amount": "117.4"},
                "fund_holders_rows": 2,
                "smart_fund_selection": {
                    "status": "succeeded",
                    "response_chars": 42,
                },
            },
            "P3_5_atomic_claim_review": {
                "queued": 1,
                "confirmed": 1,
                "failed": 0,
            },
            "P4_propose": {
                "T1": {"mode": "live", "link_count": 1, "roles": {"supports": 1}}
            },
            "P4_5_proposal_review": {
                "queued": 1,
                "confirmed": 1,
                "rejected_for_source": 0,
                "failed": 0,
                "published_evidence_links": 1,
            },
            "P5_pre_review_assessment_T1": {
                "conclusion": "supported",
                "gap_count": 0,
                "snapshot_id": str(uuid.uuid4()),
                "assessment_id": str(uuid.uuid4()),
                "mode": "live",
            },
            "P6_review": {
                "queued": 1,
                "confirmed": 1,
                "rejected": 0,
                "needs_more_evidence": 0,
                "by_thesis": {"T1": {"confirmed": 1}},
                "remaining_after_review": 0,
            },
            "P7_assessments": {
                "T1": {
                    "conclusion": "supported",
                    "gap_count": 0,
                    "snapshot_id": str(uuid.uuid4()),
                    "assessment_id": str(uuid.uuid4()),
                    "mode": "live",
                }
            },
            "P8_assessment_reviews": {
                "T1": {
                    "outcome": "confirmed",
                    "human_conclusion": None,
                    "ai_conclusion": "supported",
                }
            },
            "P9_enrichment": {
                "theme_roles": 1,
                "causal_steps": 2,
                "causal_edges": 1,
                "funds": 1,
                "holding_disclosures": 1,
                "notes": ["enrichment already applied — skipped (no dedupe key)"],
            },
            "P10_reads": {
                "overview": {"http_status": 200, "field_count": 4},
                "dossier": {"http_status": 200, "field_count": 5},
                "graph": {"http_status": 200, "nodes": 2, "edges": 1, "paths": 1},
                "gaps": {"http_status": 200, "field_count": 1},
                "knowledge": {"http_status": 200, "field_count": 1},
                "search": {"http_status": 200, "groups": {"thesis": 1}},
                "kpis": {
                    "http_status": 200,
                    "metrics": {"case_id": case_id, "throughput": {"queued": 1}},
                },
                "snapshots": {"http_status": 200, "count": 1},
                "fund_exposure": {"http_status": 200, "field_count": 1, "funds": 1},
                "metric_catalog": {"http_status": 200, "field_count": 1},
            },
            "P11_time_travel": {
                "cutoff_2024_12_31": {
                    "http_status": 404,
                    "error_code": "not_found",
                    "observation": "case_not_created_at_cutoff",
                },
                "cutoff_2025_04_01": {"http_status": 200, "supports": 1},
                "cutoff_now": {"http_status": 200, "supports": 1},
                "docs_2024_12_31": {"http_status": 200, "count": 0},
                "docs_2025_04_01": {"http_status": 200, "count": 1},
                "docs_2025_05_01": {"http_status": 200, "count": 2},
                "compare": {"http_status": 200, "field_count": 2},
            },
        }
    )
    summary["phases"]["P1_create_case"]["theses"] = {"T1": {"id": thesis_id}}
    check = {
        "check": "2025年度扭亏（隐含）",
        "ledger_fact": "PE(LYR)=337.453，隐含净利润>0",
        "verified": True,
        "source": "gildata FinQuery 2026-07-31",
    }
    summary["phases"]["P12_fact_check"] = [check]
    summary["facts"] = {
        "checks": [check],
        "quote_2026_07_31": {
            "latest_price": "1106.0",
            "total_mv": "6948.92",
            "pe_ttm": "255.759",
            "pe_lyr": "337.453",
            "pb": "42.1",
        },
        "gildata_ai_acceptance": {
            "ok": True,
            "issue_codes": [],
            "metrics": {"candidate_count": 1},
        },
    }
    summary["elapsed_seconds"] = 12.5

    facts = collect_walkthrough_facts(session, summary)

    assert facts.gildata_rounds == 2


def test_terminal_audit_replaces_its_own_issues_and_stores_only_safe_facts(
    session, monkeypatch
):
    summary, _ = _seed_accepted_walkthrough(session)
    summary["facts"] = {}
    summary["phases"]["P2_ingest"][1]["research_reports"] = 1
    old_issue = {
        "code": "gildata_ai_acceptance_ingest_replay_created_nothing",
        "source": "gildata_ai_acceptance",
    }
    summary["issues"] = [dict(old_issue), dict(old_issue)]
    monkeypatch.setattr(walkthrough, "summary", summary)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)

    walkthrough.phase_terminal_acceptance_audit(session)
    walkthrough.phase_terminal_acceptance_audit(session)

    audit = summary["facts"]["gildata_ai_acceptance"]
    assert audit["ok"] is False
    assert audit["issue_codes"] == ["ingest_replay_created_nothing"]
    assert audit["metrics"]["replay_created"] == 1
    assert all(type(value) is int for value in audit["metrics"].values())
    assert summary["issues"] == [old_issue]
    assert "寒武纪" not in repr(audit)


def test_terminal_audit_fails_closed_without_persisting_input_details(
    session, monkeypatch
):
    malformed = {
        "run_id": "audit-test",
        "phases": {"secret": "licensed-body-sentinel"},
        "issues": [
            {
                "code": "gildata_ai_acceptance_previous",
                "source": "gildata_ai_acceptance",
            }
        ],
        "facts": {},
    }
    monkeypatch.setattr(walkthrough, "summary", malformed)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)

    walkthrough.phase_terminal_acceptance_audit(session)

    audit = malformed["facts"]["gildata_ai_acceptance"]
    assert audit == {
        "ok": False,
        "issue_codes": ["audit_execution_failed"],
        "metrics": {},
    }
    assert malformed["issues"] == [
        {
            "code": "gildata_ai_acceptance_audit_execution_failed",
            "source": "gildata_ai_acceptance",
        }
    ]
    assert "licensed-body-sentinel" not in repr(audit)


def test_p9_plus_runs_terminal_audit_after_fact_check_and_before_finalize(
    monkeypatch,
):
    from app import db as app_db

    audit_session = object()
    events: list[str] = []
    state = {
        "case": {"case_id": "case-1", "theses": {}},
        "probes": {},
    }

    class _AuditSessionContext:
        def __enter__(self):
            return audit_session

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(sys, "argv", ["walkthrough", "--stages", "p9_plus"])
    monkeypatch.setattr(walkthrough, "_initialize_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(walkthrough, "_load_state", lambda: state)
    monkeypatch.setattr(walkthrough, "rec", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(walkthrough, "phase9_enrichment", lambda *_args: None)
    monkeypatch.setattr(walkthrough, "phase10_reads", lambda *_args: None)
    monkeypatch.setattr(walkthrough, "phase11_time_travel", lambda *_args: None)
    monkeypatch.setattr(
        walkthrough,
        "phase12_fact_check",
        lambda *_args: events.append("fact_check"),
    )
    monkeypatch.setattr(
        walkthrough,
        "phase_terminal_acceptance_audit",
        lambda session: events.append(
            "audit" if session is audit_session else "wrong_session"
        ),
    )
    monkeypatch.setattr(
        walkthrough,
        "_finalize",
        lambda *_args: events.append("finalize"),
    )
    monkeypatch.setattr(app_db, "SessionLocal", _AuditSessionContext)

    walkthrough.main()

    assert events == ["fact_check", "audit", "finalize"]


def _write_accepted_cli_artifacts(
    tmp_path: Path,
    *,
    run_id: str = "audit-test",
) -> tuple[Path, Path]:
    from app.models.ledger import Base

    database = tmp_path / f"evidence_walkthrough_{run_id}.db"
    summary_path = tmp_path / f"cambricon_walkthrough_{run_id}_summary.json"
    engine = create_engine(f"sqlite:///{database}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        summary, _ = _seed_accepted_walkthrough(db)
        summary["run_id"] = run_id
        db.commit()
    engine.dispose()
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return database, summary_path


def test_cli_audits_a_real_sqlite_database_without_modifying_it(tmp_path, capsys):
    database, summary_path = _write_accepted_cli_artifacts(tmp_path)
    before = database.read_bytes()

    exit_code = main(["--database", str(database), "--summary", str(summary_path)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["ok"] is True
    assert output["issue_codes"] == []
    assert output["metrics"]["candidate_count"] == 1
    assert database.read_bytes() == before


def test_cli_missing_database_fails_without_creating_it(tmp_path, capsys):
    database = tmp_path / "evidence_walkthrough_missing.db"
    summary_path = tmp_path / "cambricon_walkthrough_missing_summary.json"
    summary_path.write_text(
        json.dumps({"run_id": "missing", "phases": {}, "issues": []}),
        encoding="utf-8",
    )

    exit_code = main(["--database", str(database), "--summary", str(summary_path)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output == {
        "ok": False,
        "issue_codes": ["audit_artifact_unavailable"],
        "metrics": {},
    }
    assert not database.exists()


def test_cli_rejects_run_id_and_filename_mismatch(tmp_path, capsys):
    database, summary_path = _write_accepted_cli_artifacts(tmp_path)
    mismatched = tmp_path / "cambricon_walkthrough_other_summary.json"
    summary_path.rename(mismatched)

    exit_code = main(["--database", str(database), "--summary", str(mismatched)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["issue_codes"] == ["run_artifact_mismatch"]


@pytest.mark.parametrize("run_id", ["audit.test", "a" * 65])
def test_cli_rejects_run_ids_the_walkthrough_cannot_create(tmp_path, capsys, run_id):
    database, summary_path = _write_accepted_cli_artifacts(
        tmp_path,
        run_id=run_id,
    )

    exit_code = main(["--database", str(database), "--summary", str(summary_path)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output == {
        "ok": False,
        "issue_codes": ["invalid_summary"],
        "metrics": {},
    }


def test_cli_rejects_duplicate_summary_keys_without_echoing_content(tmp_path, capsys):
    database = tmp_path / "evidence_walkthrough_duplicate.db"
    database.touch()
    summary_path = tmp_path / "cambricon_walkthrough_duplicate_summary.json"
    summary_path.write_text(
        '{"run_id":"duplicate","run_id":"licensed-body-sentinel"}',
        encoding="utf-8",
    )

    exit_code = main(["--database", str(database), "--summary", str(summary_path)])

    rendered = capsys.readouterr().out
    output = json.loads(rendered)
    assert exit_code == 1
    assert output["issue_codes"] == ["invalid_summary"]
    assert "licensed-body-sentinel" not in rendered


def test_cli_rejects_oversized_summary_before_json_parsing(tmp_path, capsys):
    database = tmp_path / "evidence_walkthrough_large.db"
    database.touch()
    summary_path = tmp_path / "cambricon_walkthrough_large_summary.json"
    summary_path.write_bytes(b"x" * (MAX_SUMMARY_BYTES + 1))

    exit_code = main(["--database", str(database), "--summary", str(summary_path)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["issue_codes"] == ["invalid_summary"]


def test_cli_rejects_sensitive_summary_fields_without_echoing_them(tmp_path, capsys):
    database, summary_path = _write_accepted_cli_artifacts(tmp_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["facts"] = {
        "GILDATA_TOKEN": "licensed-body-sentinel",
        "body": "licensed-body-sentinel",
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    exit_code = main(["--database", str(database), "--summary", str(summary_path)])

    rendered = capsys.readouterr().out
    output = json.loads(rendered)
    assert exit_code == 1
    assert output == {
        "ok": False,
        "issue_codes": ["invalid_summary"],
        "metrics": {},
    }
    assert "licensed-body-sentinel" not in rendered
