"""Ingest command API tests (POST /api/v1/documents/ingest).

The endpoint COMMITS, so it runs against the private ``cmd_*`` engine
fixtures.  The real Gildata client is replaced via dependency override —
no network, no GILDATA_TOKEN needed for the success paths.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.api.v1.commands.ingest import get_gildata_client
from app.datasources.gildata.client import (
    GILDATA_REQUEST_ERROR_MESSAGE,
    GildataMCPError,
)
from app.main import app
from tests.test_gildata_client import _FakeClient, _make_client

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def fake_gildata(cmd_client):
    """Override the Gildata client dependency with a canned fake."""

    def _override():
        yield _make_client()

    app.dependency_overrides[get_gildata_client] = _override
    try:
        yield cmd_client
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)


def test_ingest_freezes_documents_and_valuations(fake_gildata, cmd_seeded):
    from app.models.ledger import DocumentVersion, ResearchCase, ValuationSnapshot

    seeded_vals = cmd_seeded.scalar(
        select(func.count()).select_from(ValuationSnapshot)
    )

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    resp = fake_gildata.post("/api/v1/documents/ingest", json={"case_id": str(case.id)})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["research_reports"] == 2
    assert body["research_reports_reused"] == 0
    assert body["announcements"] == 1
    assert body["announcements_reused"] == 0
    assert body["news"] == 1
    assert body["news_reused"] == 0
    assert body["spans"] == 4
    assert body["spans_reused"] == 0
    assert body["valuations_written"] == 3
    assert body["valuations_skipped"] == 0
    assert body["stock_id"] is not None
    assert body["quote_identity_verified"] is True
    # cmd_seeded has a case; omitted case_id resolves to the first case.
    assert body["case_id"] is not None

    docs = cmd_seeded.scalar(select(func.count()).select_from(DocumentVersion))
    vals = cmd_seeded.scalar(select(func.count()).select_from(ValuationSnapshot))
    assert docs >= 2  # seeded docs + newly frozen ones (hash dedupe may vary)
    assert vals == seeded_vals + 3


def test_ingest_attributes_gildata_contracts_to_the_authenticated_tenant(
    fake_gildata,
    cmd_seeded,
    monkeypatch,
):
    from app.models.ledger import DocumentVersion, ResearchCase
    from app.models.source_governance import SourceContract

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None

    response = fake_gildata.post(
        "/api/v1/documents/ingest",
        json={"case_id": str(case.id)},
    )

    assert response.status_code == 201, response.text
    contracts = list(
        cmd_seeded.scalars(
            select(SourceContract)
            .join(
                DocumentVersion,
                DocumentVersion.id == SourceContract.document_version_id,
            )
            .where(DocumentVersion.source_url.like("gildata://%"))
        )
    )
    assert len(contracts) == 4
    assert all(contract.declared_by == "tenant:test-team" for contract in contracts)
    assert all(contract.provider_or_tenant == "gildata" for contract in contracts)


def test_source_contract_declared_by_fits_a_prefixed_max_length_tenant():
    from app.models.source_governance import SourceContract

    assert SourceContract.__table__.c.declared_by.type.length >= len("tenant:") + 256


def test_ingest_preserves_a_max_length_authenticated_tenant(
    fake_gildata,
    cmd_seeded,
    monkeypatch,
):
    from app.models.ledger import (
        CaseTenantAdmission,
        DocumentVersion,
        ResearchCase,
    )
    from app.models.source_governance import SourceContract

    tenant_id = "t" * 256
    token = "long-tenant-token"
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        f'{{"{token}":"{tenant_id}"}}',
    )
    initial_document = cmd_seeded.scalar(
        select(DocumentVersion).order_by(DocumentVersion.acquired_at)
    )
    assert initial_document is not None
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="long tenant Gildata ingest",
        industry_topic="tenant-boundary",
        created_at=now,
        created_by="test",
    )
    cmd_seeded.add(case)
    cmd_seeded.flush()
    cmd_seeded.add(
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id=tenant_id,
            initial_document_version_id=initial_document.id,
            admitted_by="test-fixture",
            admitted_at=now,
        )
    )
    cmd_seeded.commit()

    response = fake_gildata.post(
        "/api/v1/documents/ingest",
        json={"case_id": str(case.id)},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201, response.text
    contracts = list(
        cmd_seeded.scalars(
            select(SourceContract)
            .join(
                DocumentVersion,
                DocumentVersion.id == SourceContract.document_version_id,
            )
            .where(DocumentVersion.source_url.like("gildata://%"))
        )
    )
    assert len(contracts) == 4
    assert {contract.declared_by for contract in contracts} == {
        f"tenant:{tenant_id}"
    }


def test_ingest_is_idempotent_via_api(fake_gildata, cmd_seeded):
    from app.models.ledger import ResearchCase, SourceSpan

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    payload = {"case_id": str(case.id)}
    first = fake_gildata.post("/api/v1/documents/ingest", json=payload)
    assert first.status_code == 201
    spans_after_first = cmd_seeded.scalar(
        select(func.count()).select_from(SourceSpan)
    )

    second = fake_gildata.post("/api/v1/documents/ingest", json=payload)
    assert second.status_code == 201, second.text
    body = second.json()
    # Valuation guard: all three metrics skipped on the second run.
    assert body["valuations_written"] == 0
    assert body["valuations_skipped"] == 3
    assert body["research_reports"] == 0
    assert body["research_reports_reused"] == 2
    assert body["announcements"] == 0
    assert body["announcements_reused"] == 1
    assert body["news"] == 0
    assert body["news_reused"] == 1
    assert body["spans"] == 0
    assert body["spans_reused"] == 4
    assert cmd_seeded.scalar(
        select(func.count()).select_from(SourceSpan)
    ) == spans_after_first


def test_ingest_unknown_case_returns_404(fake_gildata, cmd_seeded):
    resp = fake_gildata.post(
        "/api/v1/documents/ingest", json={"case_id": ZERO_UUID}
    )
    assert resp.status_code == 404


def test_ingest_requires_an_explicit_case(fake_gildata, cmd_seeded):
    resp = fake_gildata.post("/api/v1/documents/ingest", json={})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"


def test_ingest_cannot_attach_to_another_tenants_case(
    fake_gildata, cmd_seeded, monkeypatch
):
    from app.models.ledger import ResearchCase

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":"test-team","other-tenant-token":"other-team"}',
    )

    response = fake_gildata.post(
        "/api/v1/documents/ingest",
        json={"case_id": str(case.id)},
        headers={"Authorization": "Bearer other-tenant-token"},
    )

    assert response.status_code == 404


def test_ingest_without_token_returns_503(cmd_client, cmd_seeded, monkeypatch):
    """No dependency override and no GILDATA_TOKEN -> 503 envelope."""
    monkeypatch.delenv("GILDATA_TOKEN", raising=False)
    from app.models.ledger import ResearchCase

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    resp = cmd_client.post("/api/v1/documents/ingest", json={"case_id": str(case.id)})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "upstream_unavailable"


def test_ingest_provider_failure_never_echoes_upstream_details(
    cmd_client, cmd_seeded
):
    from app.models.ledger import ResearchCase

    class FailingClient:
        def call_tool(self, name, arguments, timeout=60):
            raise GildataMCPError(
                "request https://provider.invalid?token=sentinel-secret failed"
            )

    def override():
        yield FailingClient()

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post(
            "/api/v1/documents/ingest", json={"case_id": str(case.id)}
        )
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Gildata provider request failed"
    assert "sentinel-secret" not in response.text


@pytest.mark.parametrize(
    ("initial_right", "replay_right"),
    [(False, True), (True, False)],
)
def test_ingest_permission_switch_fails_closed_without_mutating_governance(
    fake_gildata,
    cmd_seeded,
    monkeypatch,
    initial_right,
    replay_right,
):
    from app.models.ledger import (
        CaseDocumentVersion,
        Company,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        Stock,
        ValuationSnapshot,
    )
    from app.models.source_governance import ProviderRecord, SourceContract

    def set_rights(value):
        configured = "true" if value else "false"
        monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", configured)
        monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", configured)

    def row_count(model):
        return cmd_seeded.scalar(select(func.count()).select_from(model))

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    payload = {
        "case_id": str(case.id),
        "research_queries": [
            "rights-query-sentinel-one",
            "rights-query-sentinel-two",
        ],
    }
    set_rights(initial_right)
    first = fake_gildata.post("/api/v1/documents/ingest", json=payload)
    assert first.status_code == 201, first.text
    tracked_models = (
        DocumentVersion,
        SourceSpan,
        CaseDocumentVersion,
        Stock,
        Company,
        ValuationSnapshot,
        SourceContract,
        ProviderRecord,
    )
    before = {model: row_count(model) for model in tracked_models}

    monkeypatch.setenv("GILDATA_TOKEN", "rights-token-sentinel")
    set_rights(replay_right)
    replay = fake_gildata.post("/api/v1/documents/ingest", json=payload)

    assert replay.status_code == 503
    error = replay.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["message"] == GILDATA_REQUEST_ERROR_MESSAGE
    assert "rights-query-sentinel" not in replay.text
    assert "rights-token-sentinel" not in replay.text
    assert "deduplicated original" not in replay.text
    assert {model: row_count(model) for model in tracked_models} == before


@pytest.mark.parametrize("corruption", ["missing", "polluted", "orphan"])
def test_ingest_provider_record_failure_rolls_back_all_ingest_tables(
    cmd_client,
    cmd_seeded,
    monkeypatch,
    corruption,
):
    from app.models.ledger import (
        CaseDocumentVersion,
        Company,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        Stock,
        ValuationSnapshot,
    )
    from app.models.source_governance import ProviderRecord, SourceContract

    target_body = "provider-record-target-body-sentinel"
    target_report = {
        "table_markdown": (
            "报告标题：Provider record 完整性目标；\n"
            "发布时间：2026-09-03；\n"
            "撰写机构：测试机构；\n"
            f"原文：{target_body}"
        )
    }
    partial_body = "partial-write-body-sentinel"
    partial_report = {
        "table_markdown": (
            "报告标题：回滚前新建的报告；\n"
            "发布时间：2026-09-03；\n"
            "撰写机构：测试机构；\n"
            f"原文：{partial_body}"
        )
    }

    def override_with(research_results):
        def override():
            yield _FakeClient([research_results], [], [])

        return override

    def row_count(model):
        return cmd_seeded.scalar(select(func.count()).select_from(model))

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    initial_payload = {
        "case_id": str(case.id),
        "research_queries": ["initial-provider-query-sentinel"],
        "announcement_query": "no-announcement",
        "news_query": "no-news",
        "quote_query": "no-quote",
    }
    app.dependency_overrides[get_gildata_client] = override_with([target_report])
    try:
        initial = cmd_client.post("/api/v1/documents/ingest", json=initial_payload)
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)
    assert initial.status_code == 201, initial.text
    provider_record = cmd_seeded.scalar(
        select(ProviderRecord)
        .join(
            DocumentVersion,
            DocumentVersion.id == ProviderRecord.document_version_id,
        )
        .where(DocumentVersion.source_url.like("gildata://research-report/%"))
    )
    assert provider_record is not None
    if corruption == "missing":
        cmd_seeded.connection().exec_driver_sql(
            "DELETE FROM provider_records WHERE id = ?",
            (provider_record.id.hex,),
        )
    elif corruption == "polluted":
        cmd_seeded.connection().exec_driver_sql(
            "UPDATE provider_records SET provider_name = ? WHERE id = ?",
            ("polluted-provider-token-sentinel", provider_record.id.hex),
        )
    else:
        source_contract = cmd_seeded.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id
                == provider_record.document_version_id
            )
        )
        assert source_contract is not None
        cmd_seeded.connection().exec_driver_sql(
            "DELETE FROM source_contracts WHERE id = ?",
            (source_contract.id.hex,),
        )
    cmd_seeded.commit()

    tracked_models = (
        DocumentVersion,
        SourceSpan,
        CaseDocumentVersion,
        Stock,
        Company,
        ValuationSnapshot,
        SourceContract,
        ProviderRecord,
    )
    before = {model: row_count(model) for model in tracked_models}
    replay_payload = {
        **initial_payload,
        "research_queries": ["replay-provider-query-sentinel"],
    }
    monkeypatch.setenv("GILDATA_TOKEN", "api-provider-token-sentinel")
    app.dependency_overrides[get_gildata_client] = override_with(
        [partial_report, target_report]
    )
    try:
        replay = cmd_client.post(
            "/api/v1/documents/ingest",
            json=replay_payload,
        )
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert replay.status_code == 503
    error = replay.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["message"] == GILDATA_REQUEST_ERROR_MESSAGE
    assert target_body not in replay.text
    assert partial_body not in replay.text
    assert "replay-provider-query-sentinel" not in replay.text
    assert "api-provider-token-sentinel" not in replay.text
    assert "polluted-provider-token-sentinel" not in replay.text
    assert {model: row_count(model) for model in tracked_models} == before


def test_ingest_cross_source_body_conflict_rolls_back_all_partial_writes(
    cmd_client,
    cmd_seeded,
):
    from app.models.ledger import (
        CaseDocumentVersion,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
    )

    shared_body = "rollback-sentinel-同一正文"
    announcement = {
        "table_markdown": (
            "|公告标题|公告日期|股票代码|公告内容|\n"
            "|---|---|---|---|\n"
            f"|同文公告|2026-09-03|688256|{shared_body}|"
        )
    }
    news = {
        "table_markdown": (
            "报告标题：同文新闻；\n"
            "发布时间：2026-09-03；\n"
            "新闻舆情来源：测试媒体；\n"
            f"原文：{shared_body}"
        )
    }

    def override():
        yield _FakeClient([], [announcement], [], news_results=[news])

    def row_count(model):
        return cmd_seeded.scalar(select(func.count()).select_from(model))

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    tracked_models = (DocumentVersion, SourceSpan, CaseDocumentVersion)
    before = {model: row_count(model) for model in tracked_models}
    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post(
            "/api/v1/documents/ingest",
            json={"case_id": str(case.id)},
        )
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["message"] == "Gildata provider request failed"
    assert shared_body not in response.text
    assert {model: row_count(model) for model in tracked_models} == before


@pytest.mark.parametrize(
    ("returned_name", "returned_code"),
    [("工业富联", "601138.SH"), ("异常上游名称", "ABC")],
)
def test_ingest_quote_identity_mismatch_returns_503_and_rolls_back(
    cmd_client,
    cmd_seeded,
    monkeypatch,
    returned_name,
    returned_code,
):
    from app.models.ledger import (
        CaseDocumentVersion,
        Company,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        Stock,
        ValuationSnapshot,
    )
    from app.models.source_governance import ProviderRecord, SourceContract

    research = {
        "table_markdown": (
            "报告标题：行情身份回滚验证；\n"
            "发布时间：2026-09-03；\n"
            "撰写机构：测试机构；\n"
            "原文：该文档必须在行情代码不一致时回滚。"
        )
    }
    mismatched_quote = (
        "|股票名称|股票代码|最新价|市盈率TTM|市净率|总市值|\n"
        "|---|---|---|---|---|---|\n"
        f"|{returned_name}|{returned_code}|50.00|20|3|2.0e11|"
    )

    def override():
        yield _FakeClient(
            [[research]],
            [],
            [{"table_markdown": mismatched_quote}],
        )

    def row_count(model):
        return cmd_seeded.scalar(select(func.count()).select_from(model))

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    tracked_models = (
        DocumentVersion,
        SourceSpan,
        CaseDocumentVersion,
        Stock,
        Company,
        ValuationSnapshot,
        SourceContract,
        ProviderRecord,
    )
    before = {model: row_count(model) for model in tracked_models}
    monkeypatch.setenv("GILDATA_TOKEN", "quote-rollback-token-sentinel")
    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post(
            "/api/v1/documents/ingest",
            json={
                "case_id": str(case.id),
                "research_queries": ["行情身份回滚验证"],
                "announcement_query": "无公告",
                "news_query": "无新闻",
                "quote_query": "寒武纪最新股价行情",
                "quote_stock_code": "688256",
            },
        )
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["message"] == GILDATA_REQUEST_ERROR_MESSAGE
    assert "quote_identity_verified" not in error
    assert returned_code not in response.text
    assert returned_name not in response.text
    assert "该文档必须在行情代码不一致时回滚" not in response.text
    assert "行情身份回滚验证" not in response.text
    assert "quote-rollback-token-sentinel" not in response.text
    assert {model: row_count(model) for model in tracked_models} == before


@pytest.mark.parametrize("invalid_code", ["", "ABC", 688256])
def test_ingest_quote_identity_invalid_request_returns_422_without_writes(
    cmd_client,
    cmd_seeded,
    invalid_code,
):
    from app.models.ledger import (
        CaseDocumentVersion,
        Company,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        Stock,
        ValuationSnapshot,
    )

    fake_client = _make_client()

    def override():
        yield fake_client

    def row_count(model):
        return cmd_seeded.scalar(select(func.count()).select_from(model))

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    tracked_models = (
        DocumentVersion,
        SourceSpan,
        CaseDocumentVersion,
        Stock,
        Company,
        ValuationSnapshot,
    )
    before = {model: row_count(model) for model in tracked_models}
    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post(
            "/api/v1/documents/ingest",
            json={
                "case_id": str(case.id),
                "quote_stock_code": invalid_code,
            },
        )
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    if isinstance(invalid_code, str):
        assert error["message"] == "quote_stock_code is invalid"
    else:
        assert error["message"] == "request validation failed"
    assert fake_client.calls == []
    assert "寒武纪" not in response.text
    assert {model: row_count(model) for model in tracked_models} == before
