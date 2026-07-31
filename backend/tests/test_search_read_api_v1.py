"""Grouped ledger search v1 read contract."""


def test_search_groups_case_thesis_and_statement(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "GPU"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert {group["object_type"] for group in payload["groups"]} >= {
        "thesis",
        "evidence",
    }
    for group in payload["groups"]:
        for hit in group["hits"]:
            assert hit["deep_link"].startswith("/")


def test_search_cutoff_excludes_future_evidence(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/search",
        params={"q": "CapEx", "cutoff": "2000-01-01T00:00:00Z", "research_mode": "true"},
    )
    assert response.status_code == 200
    evidence = next(
        (g for g in response.json()["groups"] if g["object_type"] == "evidence"),
        None,
    )
    assert evidence is None or evidence["hits"] == []


def test_search_evidence_respects_research_mode(api_client, workbench_case):
    base = {"q": "CapEx"}
    default = api_client.get("/api/v1/search", params=base)
    assert default.status_code == 200
    evidence_default = next(
        g for g in default.json()["groups"] if g["object_type"] == "evidence"
    )
    assert evidence_default["hits"] == []

    research = api_client.get(
        "/api/v1/search", params={**base, "research_mode": "true"}
    )
    assert research.status_code == 200
    evidence_research = next(
        g for g in research.json()["groups"] if g["object_type"] == "evidence"
    )
    assert len(evidence_research["hits"]) >= 1


def test_search_matches_case_title(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "compute"})
    assert response.status_code == 200
    case_group = next(
        g for g in response.json()["groups"] if g["object_type"] == "case"
    )
    assert len(case_group["hits"]) >= 1


def test_search_rejects_unknown_types(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/search", params={"q": "GPU", "types": "bogus"}
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["error"]["code"] == "validation_failed"


def test_search_validates_min_query_length(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "a"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
