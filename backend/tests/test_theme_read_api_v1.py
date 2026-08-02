"""Cross-case theme v1 read contract (横切主题 ThemeView)."""

from app.services.themes import ThemeService


def _tag(research_repository, case_id, *tags):
    case = research_repository.get_case(case_id)
    ThemeService(research_repository).apply_theme_tags(case=case, desired=list(tags))


def test_theme_list_empty_without_tags(api_client, workbench_case):
    payload = api_client.get("/api/v1/themes").json()
    assert payload["schema_version"] == "v1"
    assert payload["items"] == []


def test_theme_list_aggregates_across_cases(
    api_client, workbench_case, research_service, research_repository
):
    _tag(research_repository, workbench_case.case.id, "算力国产化")
    other = research_service.add_case(
        title="储能链", industry_topic="storage", created_by="tester"
    )
    research_service.add_thesis(other.id, statement="储能需求增长", created_by="tester")
    _tag(research_repository, other.id, "算力国产化", "锂电储能")

    payload = api_client.get("/api/v1/themes").json()
    items = {item["tag"]: item for item in payload["items"]}
    assert set(items) == {"算力国产化", "锂电储能"}
    assert items["算力国产化"]["case_count"] == 2
    assert items["算力国产化"]["company_count"] == 1
    assert items["算力国产化"]["thesis_count"] == 2
    assert items["锂电储能"]["case_count"] == 1


def test_theme_view_sections_and_derived_from(
    api_client, workbench_case, research_repository
):
    _tag(research_repository, workbench_case.case.id, "算力国产化")

    response = api_client.get("/api/v1/themes/算力国产化")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["basis"]["cutoff"]

    # workbench_case thesis: one unreviewed AI assessment -> ai_pending.
    assert len(payload["cases"]) == 1
    case = payload["cases"][0]
    assert case["case_id"] == str(workbench_case.case.id)
    assert case["thesis_counts"]["ai_pending"] == 1
    assert case["theses"][0]["ai_assessment"]["provisional"] is True
    assert case["theses"][0]["review"] is None

    roles = payload["company_roles"]
    assert len(roles) == 1
    assert roles[0]["company_code"] == "600519"
    assert roles[0]["case_title"] == workbench_case.case.title

    exposure = payload["fund_exposure"]
    assert len(exposure) == 1
    assert exposure[0]["fund_code"] == "001001"
    assert exposure[0]["stock_code"] == "600519.SH"

    derived = payload["derived_from"]
    assert derived["case_ids"] == [str(workbench_case.case.id)]
    assert derived["thesis_ids"] == [str(workbench_case.thesis.id)]
    assert len(derived["theme_role_ids"]) == 1
    assert len(derived["disclosure_ids"]) == 1


def test_theme_view_effective_judgment_follows_human_review(
    api_client, workbench_case, research_repository
):
    _tag(research_repository, workbench_case.case.id, "算力国产化")
    research_repository.insert_review(
        ai_assessment_id=workbench_case.ai_assessment.id,
        outcome="confirmed",
        conclusion=None,
        reason="证据充分",
        reviewer="tester",
    )
    payload = api_client.get("/api/v1/themes/算力国产化").json()
    counts = payload["cases"][0]["thesis_counts"]
    assert counts["supported"] == 1
    assert counts["ai_pending"] == 0
    thesis = payload["cases"][0]["theses"][0]
    # AI original stays visible alongside the human decision.
    assert thesis["ai_assessment"]["conclusion"] == "supported"
    assert thesis["review"]["outcome"] == "confirmed"


def test_theme_view_rejected_review_counted_honestly(
    api_client, workbench_case, research_repository
):
    _tag(research_repository, workbench_case.case.id, "算力国产化")
    research_repository.insert_review(
        ai_assessment_id=workbench_case.ai_assessment.id,
        outcome="rejected",
        conclusion=None,
        reason="证据不足",
        reviewer="tester",
    )
    payload = api_client.get("/api/v1/themes/算力国产化").json()
    counts = payload["cases"][0]["thesis_counts"]
    assert counts["rejected"] == 1
    assert counts["supported"] == 0


def test_theme_view_modified_review_uses_human_conclusion(
    api_client, workbench_case, research_repository
):
    _tag(research_repository, workbench_case.case.id, "算力国产化")
    research_repository.insert_review(
        ai_assessment_id=workbench_case.ai_assessment.id,
        outcome="modified",
        conclusion="contradicted",
        reason="人工修正",
        reviewer="tester",
    )
    payload = api_client.get("/api/v1/themes/算力国产化").json()
    counts = payload["cases"][0]["thesis_counts"]
    assert counts["contradicted"] == 1
    assert counts["supported"] == 0


def test_theme_view_unknown_tag_is_404(api_client):
    response = api_client.get("/api/v1/themes/元宇宙")
    assert response.status_code == 404


def test_theme_view_known_tag_without_cases_returns_empty(api_client):
    response = api_client.get("/api/v1/themes/光模块")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cases"] == []
    assert payload["company_roles"] == []
    assert payload["fund_exposure"] == []
    assert payload["derived_from"]["case_ids"] == []


def test_theme_view_cutoff_hides_later_case(
    api_client, workbench_case, research_repository
):
    _tag(research_repository, workbench_case.case.id, "算力国产化")
    payload = api_client.get(
        "/api/v1/themes/算力国产化",
        params={"cutoff": "2020-01-01T00:00:00Z"},
    ).json()
    assert payload["cases"] == []
    assert payload["company_roles"] == []
    assert payload["fund_exposure"] == []
