from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAIError
from sqlalchemy import select

from app.ai.client import LLMClient, LLMMalformedResponseError
from app.ai.research_preparation import (
    PREPARATION_PROVIDER_ERROR_MESSAGE,
    ResearchPreparationGenerator,
    ResearchPreparationProviderError,
    load_preparation_input,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseDocumentVersion,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
)
from app.models.operational import ResearchRun
from app.models.research_preparation import ResearchPreparation
from app.models.research_protocol import (
    OutcomeBindingVersion,
    VerificationRuleVersion,
)


class FakeClient:
    model_version = "fake-preparation-v1"

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[list[dict], str]] = []

    def chat_json(self, messages: list[dict], schema_hint: str) -> dict:
        self.calls.append((messages, schema_hint))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response  # type: ignore[return-value]


def _input(session, *, span_text: str | None = "Issuer disclosed revenue grew 10%."):
    now = datetime.now(UTC)
    case = ResearchCase(
        title="Preparation generator",
        industry_topic="test",
        created_by="tester",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex * 2,
        source_url="https://example.test/secret-url",
        available_at=now,
        acquired_at=now,
        parser_version="test-v1",
        source_authority="primary_disclosure",
    )
    session.add_all((case, document))
    session.flush()
    session.add(CaseDocumentVersion(
        research_case_id=case.id,
        document_version_id=document.id,
        linked_at=now,
    ))
    preparation = ResearchPreparation(
        research_case_id=case.id,
        version=3,
        input_fingerprint="f" * 64,
        status="preparing",
        parse_claims_state="queued",
        draft_protocol_state="queued",
        draft_evidence_plan_state="queued",
        claim_review_state="locked",
        protocol_review_state="locked",
        plan_review_state="locked",
        research_run_id=None,
        next_attempt_at=None,
        last_error_code=None,
        created_at=now,
        updated_at=now,
    )
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by="tester",
        change_summary="test",
        created_at=now,
    )
    session.add_all((preparation, scope))
    session.flush()
    span = None
    if span_text is not None:
        span = SourceSpan(
            document_version_id=document.id,
            locator={"source_metadata": {"api_key": "sentinel-secret"}},
            verbatim_text=span_text,
        )
        session.add(span)
        session.flush()
    session.add(EventResearchScopeFactor(
        scope_version_id=scope.id,
        statement="Revenue growth persists",
        description=None,
        position=1,
    ))
    session.add(EventResearchBrief(
        research_case_id=case.id,
        raw_input="event raw input should not be sent",
        source_url="https://example.test/also-secret",
        source_type="pasted_snapshot",
        source_metadata={"token": "sentinel-secret"},
        event_title="Test event",
        company_name=None,
        ticker=None,
        event_at=None,
        market_reaction=None,
        research_question="Why?",
        extraction_state="human_confirmed",
        created_at=now,
    ))
    session.flush()
    return case, document, span


def _protocol() -> dict:
    return {
        "outcomes": ["revenue growth"],
        "baseline": {"metric": "revenue"},
        "horizon": {"start": "2026-01-01", "end": "2026-12-31"},
        "mechanisms": ["demand"],
        "verification_rules": ["compare filings"],
        "rationale": "draft only",
    }


def _plan() -> dict:
    return {"items": [{
        "factor": "Revenue growth persists",
        "evidence_target": "quarterly revenue",
        "allowed_source_roles": ["primary_disclosure"],
        "priority": "high",
        "stop_condition": "two filings agree",
        "budget": 2,
    }]}


def test_parse_admits_candidates_without_publishing_or_creating_run(session) -> None:
    case, _, span = _input(session)
    context = load_preparation_input(session, case.id)
    response = {"statements": [{
        "source_span_id": str(span.id),
        "quote": "revenue grew 10%",
        "quote_start": span.verbatim_text.index("revenue"),
        "quote_end": span.verbatim_text.index("revenue") + len("revenue grew 10%"),
        "normalized_text": "Revenue grew by ten percent.",
        "kind": "disclosed_fact",
        "actor": "issuer",
    }]}

    payload = ResearchPreparationGenerator(FakeClient(response)).parse_claims(context, session)

    assert len(payload["candidates"]) == 1
    candidate_id = uuid.UUID(payload["candidates"][0]["candidate_id"])
    candidate = session.get(AtomicClaimCandidate, candidate_id)
    assert candidate is not None and candidate.source_span_id == span.id
    assert list(session.scalars(select(SourceStatement))) == []
    assert list(session.scalars(select(ResearchRun))) == []


def test_parse_with_no_spans_skips_llm(session) -> None:
    case, _, _ = _input(session, span_text=None)
    client = FakeClient({"statements": []})

    assert ResearchPreparationGenerator(client).parse_claims(load_preparation_input(session, case.id), session) == {"candidates": []}
    assert client.calls == []


@pytest.mark.parametrize("statement", [
    {"source_span_id": "bad", "quote": "x"},
    {"source_span_id": "00000000-0000-0000-0000-000000000000", "quote": "x", "quote_start": 0, "quote_end": 1, "normalized_text": "x", "kind": "reported_claim"},
    {"source_span_id": "span", "quote": "revenue 10%", "quote_start": 0, "quote_end": 11, "normalized_text": "x", "kind": "reported_claim"},
])
def test_parse_rejects_invalid_provider_response_atomically(session, statement) -> None:
    case, _, span = _input(session)
    if statement["source_span_id"] == "span":
        statement = {**statement, "source_span_id": str(span.id)}
    generator = ResearchPreparationGenerator(FakeClient({"statements": [statement]}))

    with pytest.raises(ResearchPreparationProviderError, match=PREPARATION_PROVIDER_ERROR_MESSAGE):
        generator.parse_claims(load_preparation_input(session, case.id), session)
    assert list(session.scalars(select(AtomicClaimCandidate))) == []


def test_protocol_returns_valid_draft_and_rejects_unknown_or_bad_horizon(session) -> None:
    case, _, _ = _input(session)
    context = load_preparation_input(session, case.id)
    assert ResearchPreparationGenerator(FakeClient(_protocol())).draft_protocol(context) == _protocol()
    for invalid in ({**_protocol(), "other": True}, {**_protocol(), "horizon": {"start": "2026-12-31", "end": "2026-01-01"}}):
        with pytest.raises(ResearchPreparationProviderError, match=PREPARATION_PROVIDER_ERROR_MESSAGE):
            ResearchPreparationGenerator(FakeClient(invalid)).draft_protocol(context)
    assert list(session.scalars(select(ResearchRun))) == []
    assert list(session.scalars(select(OutcomeBindingVersion))) == []
    assert list(session.scalars(select(VerificationRuleVersion))) == []


@pytest.mark.parametrize("invalid", [
    {"items": [{**_plan()["items"][0], "factor": "not current"}]},
    {"items": [_plan()["items"][0], _plan()["items"][0]]},
    {"items": [{**_plan()["items"][0], "budget": 0}]},
])
def test_plan_requires_current_unique_factor_and_positive_budget(session, invalid) -> None:
    case, _, _ = _input(session)
    context = load_preparation_input(session, case.id)
    assert ResearchPreparationGenerator(FakeClient(_plan())).draft_evidence_plan(context) == _plan()
    with pytest.raises(ResearchPreparationProviderError, match=PREPARATION_PROVIDER_ERROR_MESSAGE):
        ResearchPreparationGenerator(FakeClient(invalid)).draft_evidence_plan(context)


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("sentinel-secret"),
    OpenAIError("sentinel-secret"),
    LLMMalformedResponseError("bad"),
])
def test_known_provider_failures_are_safe_but_programming_errors_pass_through(session, failure) -> None:
    case, _, _ = _input(session)
    context = load_preparation_input(session, case.id)
    with pytest.raises(ResearchPreparationProviderError, match=PREPARATION_PROVIDER_ERROR_MESSAGE) as raised:
        ResearchPreparationGenerator(FakeClient(failure)).draft_protocol(context)
    assert "sentinel-secret" not in str(raised.value)
    for error in (TypeError, KeyError, AssertionError):
        with pytest.raises(error, match="programming defect"):
            ResearchPreparationGenerator(FakeClient(error("programming defect"))).draft_protocol(context)


@pytest.mark.parametrize("response", [SimpleNamespace(choices=[]), SimpleNamespace(choices=[SimpleNamespace()]), SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())])])
def test_llm_client_malformed_choices_are_named_errors(response) -> None:
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)))
    with pytest.raises(LLMMalformedResponseError):
        LLMClient(model_version="test", client=sdk).chat_json([])


def test_prompts_are_drafts_and_user_payload_excludes_source_metadata(session) -> None:
    case, _, _ = _input(session)
    client = FakeClient(_protocol())
    ResearchPreparationGenerator(client).draft_protocol(load_preparation_input(session, case.id))
    system = client.calls[0][0][0]["content"].lower()
    user = client.calls[0][0][1]["content"]
    assert "draft" in system and "authorization" in system
    assert "sentinel-secret" not in user
    assert json.loads(user)
