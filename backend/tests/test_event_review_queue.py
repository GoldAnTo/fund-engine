from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select

from app.models.events import DomainEvent
from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.proposals import Proposal
from app.models.source_governance import SourceContract
from app.repositories.event_research import EventResearchLifecycleRepository
from app.repositories.operational import TaskRepository
from app.schemas.v1.event_research import EventReviewQueueItemDTO
from app.services.auto_research import AutoResearchService
from app.services.event_review_queue import EventReviewQueueService


def _seed_evidence_proposal(
    session,
    *,
    case: ResearchCase,
    proposed_at: datetime,
    source_url: str,
    parser_version: str = "html-v1",
    parse_state: str = "success",
    status: str = "pending",
    thesis_statement: str | None = None,
    source_title: str | None = None,
    statement_text: str = "Evidence statement",
    verbatim_text: str = "Evidence excerpt",
    locator: dict | None = None,
    proposal_reason: str = "supports the factor",
    proposal_scope: dict | None = None,
    published_at: datetime | None = None,
) -> Proposal:
    thesis = Thesis(
        research_case_id=case.id,
        statement=(
            thesis_statement
            if thesis_statement is not None
            else f"factor-{source_url}"
        ),
        created_by="tester",
        created_at=proposed_at,
    )
    document = DocumentVersion(
        content_sha256=hashlib.sha256(source_url.encode()).hexdigest(),
        source_url=source_url,
        title=source_title if source_title is not None else f"Source for {source_url}",
        published_at=published_at,
        available_at=proposed_at,
        acquired_at=proposed_at,
        parser_version=parser_version,
        parse_state=parse_state,
    )
    session.add_all([thesis, document])
    session.flush()
    session.add(
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=proposed_at,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text=verbatim_text,
        locator=locator if locator is not None else {"page": 1},
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text=statement_text,
        created_at=proposed_at,
    )
    session.add(statement)
    session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": proposal_reason,
            "scope": (
                proposal_scope if proposal_scope is not None else {"period": "event"}
            ),
        },
        target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="test",
        proposed_at=proposed_at,
        research_case_id=case.id,
        status=status,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _document_for_proposal(session, proposal: Proposal) -> DocumentVersion:
    statement = session.get(
        SourceStatement,
        uuid.UUID(proposal.payload["source_statement_id"]),
    )
    assert statement is not None
    span = session.get(SourceSpan, statement.source_span_id)
    assert span is not None
    document = session.get(DocumentVersion, span.document_version_id)
    assert document is not None
    return document


def _add_gildata_contract(
    session,
    proposal: Proposal,
    *,
    allow_ai_processing: bool = True,
    allow_display: bool = True,
    provider_or_tenant: str = "gildata",
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
) -> SourceContract:
    document = _document_for_proposal(session, proposal)
    contract = SourceContract(
        document_version_id=document.id,
        source_type="licensed_provider",
        research_source_type="licensed_provider",
        provider_or_tenant=provider_or_tenant,
        allow_ai_processing=allow_ai_processing,
        allow_display=allow_display,
        allow_export=False,
        allow_api=False,
        region="cn",
        effective_from=effective_from,
        effective_until=effective_until,
        retention_policy="case_retained",
        deletion_policy="manual",
        downstream_restrictions=["仅限测试 Case"],
        contract_version="test-v1",
        intake_metadata={},
        declared_by="human:test",
        created_at=datetime.now(UTC),
    )
    session.add(contract)
    session.flush()
    return contract


def _seed_sensitive_polluted_gildata_proposal(
    session,
    *,
    case: ResearchCase,
    source_url: str,
) -> Proposal:
    proposal = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=datetime.now(UTC),
        source_url=source_url,
        thesis_statement="polluted Gildata audit factor",
        source_title="polluted-gildata-derived-sentinel title",
        statement_text="polluted-gildata-derived-sentinel statement",
        verbatim_text="polluted-gildata-derived-sentinel excerpt",
        locator={"sentinel": "polluted-gildata-derived-sentinel locator"},
        proposal_reason="polluted-gildata-derived-sentinel reason",
        proposal_scope={"excerpt": "polluted-gildata-derived-sentinel scope"},
    )
    return proposal


def _seed_sensitive_legacy_gildata_link(
    session,
    *,
    case: ResearchCase,
    source_url: str,
    parser_version: str = "gildata-mcp-1",
) -> tuple[EvidenceLink, Proposal]:
    """Create a legacy machine-generated link with deliberately sensitive text."""
    now = datetime.now(UTC)
    proposal = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now,
        source_url=source_url,
        parser_version=parser_version,
        status="decided",
        thesis_statement="legacy-gildata-derived-sentinel thesis",
        source_title="legacy-gildata-derived-sentinel title",
        statement_text="legacy-gildata-derived-sentinel statement",
        verbatim_text="legacy-gildata-derived-sentinel excerpt",
        locator={"sentinel": "legacy-gildata-derived-sentinel locator"},
        proposal_reason="legacy proposal is already decided",
        published_at=now - timedelta(days=7),
    )
    statement = session.get(
        SourceStatement,
        uuid.UUID(proposal.payload["source_statement_id"]),
    )
    assert statement is not None
    thesis = session.get(
        Thesis,
        uuid.UUID(proposal.target_context["thesis_id"]),
    )
    assert thesis is not None
    link = EvidenceLink(
        thesis_id=thesis.id,
        source_statement_id=statement.id,
        role="supports",
        reason="legacy-gildata-derived-sentinel reason",
        scope={"excerpt": "legacy-gildata-derived-sentinel scope"},
        available_at=now,
        creator_type="ai",
        review_state="machine_generated",
        created_at=now,
    )
    session.add(link)
    session.flush()
    return link, proposal


def _assert_legacy_review_item_is_audit_shell(item: dict, link: EvidenceLink) -> None:
    assert item["link_id"] == str(link.id)
    assert item["thesis_id"] == str(link.thesis_id)
    assert item["case_id"]
    assert item["statement_id"] == str(link.source_statement_id)
    assert item["span_id"]
    assert item["document_version_id"]
    assert item["ai_role"] == "supports"
    assert item["statement_kind"] == "fact"
    assert item["thesis_statement"] == ""
    assert item["ai_reason"] == ""
    assert item["ai_scope"] == {}
    assert item["statement_text"] == ""
    assert item["verbatim_text"] == ""
    assert item["locator"] == {}
    assert item["document_source_url"] == ""
    assert item["document_published_at"] is None
    assert item["available_at"] == ""


def _case(session) -> ResearchCase:
    now = datetime.now(UTC)
    case = ResearchCase(
        title="event evidence queue",
        industry_topic="event",
        created_by="tester",
        created_at=now,
    )
    session.add(case)
    session.flush()
    initial_document = DocumentVersion(
        content_sha256=hashlib.sha256(b"initial event intake").hexdigest(),
        source_url="event://initial-intake",
        title="Initial event intake",
        available_at=now,
        acquired_at=now,
        parser_version="user-pasted-v1",
        parse_state="partial",
    )
    session.add(initial_document)
    session.flush()
    session.add(
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id="test-team",
            initial_document_version_id=initial_document.id,
            admitted_by="tester",
            admitted_at=now,
        )
    )
    session.add(
        EventResearchLifecycle(
            research_case_id=case.id,
            status="awaiting_key_review",
            current_round=2,
            status_summary="waiting for evidence review",
            next_human_action="审核 1 条关键证据",
            updated_at=now,
        )
    )
    return case


def test_event_review_queue_schema_rejects_unknown_source_status() -> None:
    with pytest.raises(ValidationError):
        EventReviewQueueItemDTO(
            proposal_id="proposal-1",
            status="pending",
            proposed_at=datetime.now(UTC),
            link_id="link-1",
            thesis_id=None,
            case_id="case-1",
            thesis_statement=None,
            ai_role="supports",
            ai_reason="reason",
            ai_scope={},
            statement_id=None,
            statement_text=None,
            statement_kind=None,
            span_id=None,
            verbatim_text=None,
            locator={},
            document_version_id=None,
            document_source_url=None,
            document_published_at=None,
            available_at=None,
            source_title=None,
            source_status="retired",
            source_status_reason="unknown state",
            can_accept=False,
            proposal_reason="reason",
            position=None,
        )


def test_event_review_queue_summarizes_pending_invalid_and_reviewed_items(
    cmd_client, cmd_session
) -> None:
    case = _case(cmd_session)
    now = datetime.now(UTC)
    valid = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now,
        source_url="https://investor.tsmc.com/english/quarterly-results/valid",
    )
    invalid = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now + timedelta(seconds=1),
        source_url="https://example.com/invalid",
    )
    _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now + timedelta(seconds=2),
        source_url="https://investor.tsmc.com/english/quarterly-results/reviewed",
        status="decided",
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/review-queue")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == {
        "total": 3,
        "reviewed": 1,
        "pending": 1,
        "invalid_source": 1,
        "current_round": 2,
        "next_action": "审核 1 条关键证据",
    }
    assert [item["proposal_id"] for item in body["items"]] == [
        str(valid.id),
        str(invalid.id),
    ]
    invalid_item = next(
        item for item in body["items"] if item["proposal_id"] == str(invalid.id)
    )
    assert invalid_item["source_title"] == "Source for https://example.com/invalid"
    assert invalid_item["source_status"] == "invalid"
    assert invalid_item["can_accept"] is False
    assert "测试域名" in invalid_item["source_status_reason"]
    assert invalid_item["proposal_reason"] == "supports the factor"
    assert invalid_item["position"] is None


def test_event_review_summary_uses_the_shared_gildata_contract_admission(
    cmd_session,
) -> None:
    case = _case(cmd_session)
    now = datetime.now(UTC)
    authorised = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now,
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
    )
    restricted = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now + timedelta(seconds=1),
        source_url="gildata://announcement/" + "b" * 64,
        parser_version="gildata-mcp-1",
    )
    wrong_identity = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now + timedelta(seconds=2),
        source_url="gildata://news/" + "c" * 64,
        parser_version="gildata-mcp-1",
    )
    _add_gildata_contract(cmd_session, authorised)
    _add_gildata_contract(cmd_session, restricted, allow_display=False)
    _add_gildata_contract(
        cmd_session,
        wrong_identity,
        provider_or_tenant="other-provider",
    )
    cmd_session.commit()

    summary = EventReviewQueueService(cmd_session).summary(case.id)

    assert summary.total == 3
    assert summary.pending == 1
    assert summary.invalid_source == 1


@pytest.mark.parametrize(
    ("contract_overrides", "reason_fragment"),
    [
        ({"allow_display": False}, "禁止展示"),
        (
            {
                "effective_until": datetime(
                    2020,
                    1,
                    1,
                    tzinfo=UTC,
                )
            },
            "已失效",
        ),
    ],
)
def test_event_review_queue_redacts_gildata_when_contract_restricts_display(
    cmd_client,
    cmd_session,
    contract_overrides: dict,
    reason_fragment: str,
) -> None:
    case = _case(cmd_session)
    now = datetime.now(UTC)
    proposal = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now,
        source_url="gildata://research-report/" + "d" * 64,
        parser_version="gildata-mcp-1",
    )
    proposal.payload = {
        **proposal.payload,
        "reason": "restricted-derived-reason-sentinel",
        "scope": {"excerpt": "restricted-derived-scope-sentinel"},
    }
    _add_gildata_contract(cmd_session, proposal, **contract_overrides)
    cmd_session.commit()

    event_response = cmd_client.get(
        f"/api/v1/event-research/{case.id}/review-queue"
    )

    assert event_response.status_code == 200, event_response.text
    event_item = next(
        item
        for item in event_response.json()["items"]
        if item["proposal_id"] == str(proposal.id)
    )
    assert event_item["source_status"] == "restricted"
    assert event_item["can_accept"] is False
    assert reason_fragment in event_item["source_status_reason"]
    assert event_item["ai_role"] == "supports"
    assert event_item["statement_kind"] == "fact"
    assert event_item["ai_reason"] == ""
    assert event_item["proposal_reason"] == ""
    assert event_item["ai_scope"] == {}
    assert event_item["statement_id"] is not None
    assert event_item["span_id"] is not None
    assert event_item["document_version_id"] is not None
    assert event_item["statement_text"] is None
    assert event_item["verbatim_text"] is None
    assert event_item["locator"] == {}
    assert event_item["document_source_url"] is None
    assert event_item["source_title"] is None
    assert event_item["document_published_at"] is None
    assert event_item["available_at"] is None
    assert "restricted-derived" not in event_response.text


@pytest.mark.parametrize(
    "contract_overrides",
    [
        {"allow_display": False},
        {
            "effective_until": datetime(
                2020,
                1,
                1,
                tzinfo=UTC,
            )
        },
    ],
)
def test_general_review_queue_redacts_gildata_when_contract_restricts_display(
    cmd_client,
    cmd_session,
    contract_overrides: dict,
) -> None:
    case = _case(cmd_session)
    proposal = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=datetime.now(UTC),
        source_url="gildata://macro-industry/" + "1" * 64,
        parser_version="gildata-mcp-1",
    )
    proposal.payload = {
        **proposal.payload,
        "reason": "restricted-derived-reason-sentinel",
        "scope": {"excerpt": "restricted-derived-scope-sentinel"},
    }
    _add_gildata_contract(cmd_session, proposal, **contract_overrides)
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/review-queue?case_id={case.id}")

    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["link_id"] == str(proposal.id)
    )
    assert item["statement_id"] is not None
    assert item["span_id"] is not None
    assert item["document_version_id"] is not None
    assert item["ai_role"] == "supports"
    assert item["statement_kind"] == "fact"
    assert item["ai_reason"] == ""
    assert item["ai_scope"] == {}
    assert item["statement_text"] == ""
    assert item["verbatim_text"] == ""
    assert item["locator"] == {}
    assert item["document_source_url"] == ""
    assert item["document_published_at"] is None
    assert item["available_at"] == ""
    assert "restricted-derived" not in response.text


def test_gildata_ai_restriction_does_not_hide_display_allowed_review_content(
    cmd_client,
    cmd_session,
) -> None:
    case = _case(cmd_session)
    proposal = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=datetime.now(UTC),
        source_url="gildata://announcement/" + "e" * 64,
        parser_version="gildata-mcp-1",
    )
    _add_gildata_contract(
        cmd_session,
        proposal,
        allow_ai_processing=False,
        allow_display=True,
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/review-queue")

    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["proposal_id"] == str(proposal.id)
    )
    assert item["source_status"] == "restricted"
    assert item["can_accept"] is False
    assert "禁止 AI 处理" in item["source_status_reason"]
    assert item["statement_text"] == "Evidence statement"
    assert item["verbatim_text"] == "Evidence excerpt"
    assert item["locator"] == {"page": 1}
    assert item["document_source_url"].startswith("gildata://announcement/")
    assert item["source_title"].startswith("Source for gildata://announcement/")


@pytest.mark.parametrize("contract_state", ["missing", "wrong_provider"])
def test_review_queues_redact_gildata_without_trusted_contract_identity(
    cmd_client,
    cmd_session,
    contract_state: str,
) -> None:
    case = _case(cmd_session)
    proposal = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=datetime.now(UTC),
        source_url="gildata://news/" + "f" * 64,
        parser_version="gildata-mcp-1",
    )
    if contract_state == "wrong_provider":
        _add_gildata_contract(
            cmd_session,
            proposal,
            provider_or_tenant=" gildata",
        )
    cmd_session.commit()

    event_response = cmd_client.get(
        f"/api/v1/event-research/{case.id}/review-queue"
    )

    assert event_response.status_code == 200, event_response.text
    event_item = next(
        item
        for item in event_response.json()["items"]
        if item["proposal_id"] == str(proposal.id)
    )
    assert event_item["source_status"] == "invalid"
    assert event_item["can_accept"] is False
    assert event_item["statement_text"] is None
    assert event_item["verbatim_text"] is None
    assert event_item["locator"] == {}
    assert event_item["document_source_url"] is None
    assert event_item["source_title"] is None

    queue_response = cmd_client.get(f"/api/v1/review-queue?case_id={case.id}")

    assert queue_response.status_code == 200, queue_response.text
    queue_item = next(
        item
        for item in queue_response.json()["items"]
        if item["link_id"] == str(proposal.id)
    )
    assert queue_item["statement_text"] == ""
    assert queue_item["verbatim_text"] == ""
    assert queue_item["locator"] == {}
    assert queue_item["document_source_url"] == ""


@pytest.mark.parametrize(
    "source_url",
    [
        "gil\ndata://research-report/" + "2" * 64,
        "gild\tata://research-report/" + "2" * 64,
        "gildata\r://research-report/" + "2" * 64,
    ],
    ids=["embedded-lf", "embedded-tab", "embedded-cr"],
)
def test_event_review_queue_redacts_whatwg_polluted_gildata_scheme(
    cmd_client,
    cmd_session,
    source_url: str,
) -> None:
    case = _case(cmd_session)
    proposal = _seed_sensitive_polluted_gildata_proposal(
        cmd_session,
        case=case,
        source_url=source_url,
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/review-queue")

    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["proposal_id"] == str(proposal.id)
    )
    assert item["source_status"] == "invalid"
    assert item["can_accept"] is False
    assert item["statement_id"] is not None
    assert item["span_id"] is not None
    assert item["document_version_id"] is not None
    assert item["ai_role"] == "supports"
    assert item["statement_kind"] == "fact"
    assert item["ai_reason"] == ""
    assert item["proposal_reason"] == ""
    assert item["ai_scope"] == {}
    assert item["statement_text"] is None
    assert item["verbatim_text"] is None
    assert item["locator"] == {}
    assert item["document_source_url"] is None
    assert item["source_title"] is None
    assert "polluted-gildata-derived-sentinel" not in response.text


@pytest.mark.parametrize(
    "source_url",
    [
        "gil\ndata://research-report/" + "3" * 64,
        "gild\tata://research-report/" + "3" * 64,
        "gildata\r://research-report/" + "3" * 64,
    ],
    ids=["embedded-lf", "embedded-tab", "embedded-cr"],
)
def test_general_review_queue_redacts_whatwg_polluted_gildata_scheme(
    cmd_client,
    cmd_session,
    source_url: str,
) -> None:
    case = _case(cmd_session)
    proposal = _seed_sensitive_polluted_gildata_proposal(
        cmd_session,
        case=case,
        source_url=source_url,
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/review-queue?case_id={case.id}")

    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["link_id"] == str(proposal.id)
    )
    assert item["statement_id"] is not None
    assert item["span_id"] is not None
    assert item["document_version_id"] is not None
    assert item["ai_role"] == "supports"
    assert item["statement_kind"] == "fact"
    assert item["ai_reason"] == ""
    assert item["ai_scope"] == {}
    assert item["statement_text"] == ""
    assert item["verbatim_text"] == ""
    assert item["locator"] == {}
    assert item["document_source_url"] == ""
    assert "polluted-gildata-derived-sentinel" not in response.text


@pytest.mark.parametrize(
    "contract_state",
    ["display_denied", "expired", "not_yet_effective", "missing"],
)
def test_legacy_review_queue_redacts_gildata_when_contract_cannot_display(
    cmd_client,
    cmd_session,
    contract_state: str,
) -> None:
    case = _case(cmd_session)
    link, proposal = _seed_sensitive_legacy_gildata_link(
        cmd_session,
        case=case,
        source_url="gildata://research-report/" + "4" * 64,
    )
    now = datetime.now(UTC)
    if contract_state != "missing":
        overrides = {
            "display_denied": {"allow_display": False},
            "expired": {"effective_until": now - timedelta(days=1)},
            "not_yet_effective": {"effective_from": now + timedelta(days=1)},
        }[contract_state]
        _add_gildata_contract(cmd_session, proposal, **overrides)
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/review-queue?case_id={case.id}")

    assert response.status_code == 200, response.text
    item = next(
        item for item in response.json()["items"] if item["link_id"] == str(link.id)
    )
    _assert_legacy_review_item_is_audit_shell(item, link)
    assert "legacy-gildata-derived-sentinel" not in response.text


@pytest.mark.parametrize(
    "source_url",
    [
        "gil\ndata://research-report/" + "5" * 64,
        "gild\tata://research-report/" + "5" * 64,
        "gildata\r://research-report/" + "5" * 64,
        "\x00 gildata://research-report/" + "5" * 64,
        "gildata://research-report/" + "5" * 64 + " \x1f",
    ],
    ids=["embedded-lf", "embedded-tab", "embedded-cr", "leading-c0", "trailing-c0"],
)
def test_legacy_review_queue_redacts_polluted_gildata_scheme(
    cmd_client,
    cmd_session,
    source_url: str,
) -> None:
    case = _case(cmd_session)
    link, _proposal = _seed_sensitive_legacy_gildata_link(
        cmd_session,
        case=case,
        source_url=source_url,
        parser_version="html-v1",
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/review-queue?case_id={case.id}")

    assert response.status_code == 200, response.text
    item = next(
        item for item in response.json()["items"] if item["link_id"] == str(link.id)
    )
    _assert_legacy_review_item_is_audit_shell(item, link)
    assert "legacy-gildata-derived-sentinel" not in response.text


def test_legacy_review_queue_redacts_gildata_parser_signal_without_contract(
    cmd_client,
    cmd_session,
) -> None:
    case = _case(cmd_session)
    link, _proposal = _seed_sensitive_legacy_gildata_link(
        cmd_session,
        case=case,
        source_url="https://investor.tsmc.com/gildata-derived-report",
        parser_version="GILDATA-MCP-1",
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/review-queue?case_id={case.id}")

    assert response.status_code == 200, response.text
    item = next(
        item for item in response.json()["items"] if item["link_id"] == str(link.id)
    )
    _assert_legacy_review_item_is_audit_shell(item, link)
    assert "legacy-gildata-derived-sentinel" not in response.text


def test_legacy_review_queue_batches_contracts_at_fixed_query_count(
    cmd_session,
) -> None:
    from app.queries.review_queue import ReviewQueueQueries

    case = _case(cmd_session)
    case_id = case.id

    def add_link(index: int) -> None:
        digest = f"{index:064x}"
        _link, proposal = _seed_sensitive_legacy_gildata_link(
            cmd_session,
            case=case,
            source_url=f"gildata://news/{digest}",
        )
        _add_gildata_contract(cmd_session, proposal)

    def select_counts() -> tuple[int, int]:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        cmd_session.expire_all()
        sqlalchemy_event.listen(cmd_session.get_bind(), "before_cursor_execute", record)
        try:
            ReviewQueueQueries(cmd_session).list_items(case_id=case_id, limit=50)
        finally:
            sqlalchemy_event.remove(
                cmd_session.get_bind(),
                "before_cursor_execute",
                record,
            )
        contract_selects = sum("source_contracts" in item for item in statements)
        return len(statements), contract_selects

    add_link(1)
    cmd_session.commit()
    one_link_counts = select_counts()

    for index in range(2, 8):
        add_link(index)
    cmd_session.commit()
    seven_link_counts = select_counts()

    assert one_link_counts[1] == 1
    assert seven_link_counts == one_link_counts


def test_event_review_summary_batches_contracts_at_fixed_query_count(
    cmd_session,
) -> None:
    case = _case(cmd_session)
    case_id = case.id
    now = datetime.now(UTC)

    def add_proposal(index: int) -> None:
        digest = f"{index:064x}"
        proposal = _seed_evidence_proposal(
            cmd_session,
            case=case,
            proposed_at=now + timedelta(seconds=index),
            source_url=f"gildata://research-report/{digest}",
            parser_version="gildata-mcp-1",
        )
        _add_gildata_contract(cmd_session, proposal)

    def select_counts() -> tuple[int, int]:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        cmd_session.expire_all()
        sqlalchemy_event.listen(cmd_session.get_bind(), "before_cursor_execute", record)
        try:
            EventReviewQueueService(cmd_session).summary(case_id)
        finally:
            sqlalchemy_event.remove(
                cmd_session.get_bind(),
                "before_cursor_execute",
                record,
            )
        contract_selects = sum("source_contracts" in item for item in statements)
        return len(statements), contract_selects

    add_proposal(1)
    cmd_session.commit()
    one_proposal_counts = select_counts()

    for index in range(2, 8):
        add_proposal(index)
    cmd_session.commit()
    seven_proposal_counts = select_counts()

    assert one_proposal_counts[1] == 1
    assert seven_proposal_counts == one_proposal_counts


def test_reconcile_invalid_source_closes_only_its_task_and_lifecycle_counts_valid_items(
    session,
) -> None:
    case = _case(session)
    now = datetime.now(UTC)
    valid = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now,
        source_url="https://investor.tsmc.com/english/quarterly-results/valid",
    )
    invalid = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now + timedelta(seconds=1),
        source_url="https://example.com/invalid",
    )
    tasks = TaskRepository(session)
    valid_task = tasks.add_task(
        title="review valid",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=valid.id,
        research_case_id=case.id,
    )
    invalid_task = tasks.add_task(
        title="review invalid",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=invalid.id,
        research_case_id=case.id,
    )
    session.commit()

    reconciled = EventReviewQueueService(session).reconcile_event_review_queue(case.id)
    session.commit()

    session.refresh(valid_task)
    session.refresh(invalid_task)
    assert reconciled.invalid_source_proposal_ids == [invalid.id]
    assert valid_task.status == "open"
    assert invalid_task.status == "done"
    assert session.get(Proposal, invalid.id).status == "pending"
    assert EventResearchLifecycleRepository(session).pending_key_review_count(case.id) == 1
    audit = session.scalar(
        select(DomainEvent).where(DomainEvent.aggregate_id == str(invalid.id))
    )
    assert audit is not None
    assert audit.payload["admission_reason"] == (
        "来源为测试域名，不能作为有效证据来源。"
    )


def test_lifecycle_refresh_reconciles_invalid_source_review_tasks(session) -> None:
    case = _case(session)
    now = datetime.now(UTC)
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=2,
        max_rounds=3,
        budget=10,
        budget_used=1,
        stop_reason="budget_exhausted",
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    lifecycle = session.get(EventResearchLifecycle, case.id)
    lifecycle.active_run_id = run.id
    invalid = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now,
        source_url=f"https://example.com/invalid-{case.id}",
    )
    task = TaskRepository(session).add_task(
        title="review invalid",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=invalid.id,
        research_case_id=case.id,
    )
    session.commit()

    AutoResearchService(session).refresh_event_lifecycle(run)
    session.commit()

    session.refresh(task)
    assert task.status == "done"
    audit = session.scalar(
        select(DomainEvent).where(DomainEvent.aggregate_id == str(invalid.id))
    )
    assert audit is not None
