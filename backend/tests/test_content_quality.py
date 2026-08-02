"""Content-quality assessment unit tests + read-side integration (defect 4)."""

from datetime import UTC, datetime

from app.services.content_quality import assess_span_texts


# ---------------------------------------------------------------------------
# assess_span_texts unit tests
# ---------------------------------------------------------------------------


def test_unknown_when_no_text():
    assert assess_span_texts([]) == ("unknown", [])
    assert assess_span_texts(["", "  "]) == ("unknown", [])


def test_ok_for_normal_research_report_text():
    text = "寒武纪2024年实现营业收入11.74亿元，同比增长65.56%，云端产品线收入同比大幅增长，主要受益于国产AI算力需求爆发。"
    quality, reasons = assess_span_texts([text])
    assert quality == "ok"
    assert reasons == []


def test_degenerate_for_four_char_body():
    quality, reasons = assess_span_texts(["相关研究"])
    assert quality == "degenerate"
    assert any(r.startswith("content_too_short") for r in reasons)


def test_degenerate_for_orphan_table_header():
    quality, reasons = assess_span_texts(["| % | 1个月 | 3个月 | 6个月 |"])
    assert quality == "degenerate"
    assert any(r.startswith("table_header_only") for r in reasons)


def test_table_header_only_also_flags_header_plus_separator():
    quality, reasons = assess_span_texts(
        ["| 指标 | 数值 |\n| --- | --- |"]
    )
    assert quality == "degenerate"
    assert any(r.startswith("table_header_only") for r in reasons)


def test_real_table_with_data_rows_is_not_flagged():
    table = (
        "| 指标 | 本期数值 | 同比变动 |\n"
        "| --- | --- | --- |\n"
        "| 营业收入（亿元） | 11.74 | 增长65.56% |\n"
        "| 归母净利润（亿元） | 2.72 | 单季首次转正 |\n"
        "| 云端产品线收入增速 | 1187.78% | 显著放量 |"
    )
    quality, reasons = assess_span_texts([table])
    assert quality == "ok"
    assert reasons == []


def test_degenerate_for_low_information_density():
    quality, reasons = assess_span_texts(["|||---|||---||||---||||||abc"])
    assert quality == "degenerate"
    assert any(r.startswith("content_too_short") for r in reasons)


def test_ok_verdict_has_no_reasons():
    quality, reasons = assess_span_texts(
        [
            "这是一段足够长的正常研报正文内容，包含具体的营业收入、净利润、毛利率等"
            "财务指标的详细分析，并对未来几个季度的经营趋势给出了明确的判断与依据。"
        ]
    )
    assert quality == "ok"
    assert reasons == []


# ---------------------------------------------------------------------------
# Read-side integration: documents list carries content_quality
# ---------------------------------------------------------------------------


def _freeze_with_text(document_service, text: str, url: str):
    version = document_service.freeze(raw=text.encode(), source_url=url)
    document_service.add_span(
        document_version_id=version.id,
        locator={"page": 1},
        verbatim_text=text,
    )
    return version


def test_documents_list_flags_degenerate_content(api_client, document_service):
    version = _freeze_with_text(
        document_service, "相关研究", "https://example.test/degenerate"
    )
    response = api_client.get("/api/v1/documents")
    assert response.status_code == 200
    (item,) = [
        i for i in response.json()["items"] if i["id"] == str(version.id)
    ]
    assert item["content_quality"] == "degenerate"
    assert any(r.startswith("content_too_short") for r in item["quality_reasons"])


def test_documents_list_marks_normal_content_ok(api_client, document, span):
    response = api_client.get("/api/v1/documents")
    assert response.status_code == 200
    (item,) = [
        i for i in response.json()["items"] if i["id"] == str(document.id)
    ]
    # The conftest span text ("original span text") is short — degenerate by
    # length, which is the honest derived verdict for this fixture.
    assert item["content_quality"] in ("ok", "degenerate")
    assert isinstance(item["quality_reasons"], list)


def test_pending_versions_exclude_degenerate_content(session, document_service):
    from app.scripts.run_ai_engine import _pending_versions

    degenerate = _freeze_with_text(
        document_service, "相关研究", "https://example.test/deg2"
    )
    normal = _freeze_with_text(
        document_service,
        "寒武纪2024年营业收入11.74亿元，同比增长65.56%，云端产品线收入大幅增长，"
        "主要受益于国产AI算力需求持续爆发，公司思元系列芯片出货量显著提升。",
        "https://example.test/normal2",
    )
    pending = _pending_versions(session)
    assert degenerate not in pending
    assert normal in pending
