"""Global library reads use admitted Case ownership, including nested citations."""


def test_library_requires_credentials_and_owned_attachment(
    api_client, session, research_service, document_service, document, monkeypatch
):
    from tests.tenant_admission import admit_case

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign-token":"foreign-team"}')
    case = research_service.add_case(title="owner", industry_topic="test", created_by="u")
    admit_case(session, case.id, document_version_id=document.id)
    orphan = document_service.freeze(raw=b"unattached", source_url="https://example.test/orphan")
    for path in ("/api/v1/documents", f"/api/v1/documents/{document.id}"):
        assert api_client.get(path, headers={"Authorization": ""}).status_code == 401
        assert api_client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 403
    assert api_client.get(f"/api/v1/documents/{document.id}").status_code == 200
    assert api_client.get(f"/api/v1/documents/{orphan.id}").status_code == 404
    foreign_headers = {"Authorization": "Bearer foreign-token"}
    assert api_client.get(f"/api/v1/documents/{document.id}", headers=foreign_headers).status_code == 404
    response = api_client.get("/api/v1/documents", headers=foreign_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["page"] == {"next_cursor": None, "has_more": False}
    assert api_client.get("/api/v1/documents", params={"case_id": str(case.id)}, headers=foreign_headers).status_code == 404


def test_library_shared_document_only_exposes_owned_case_citations(
    api_client, session, research_service, document, span, monkeypatch
):
    from tests.tenant_admission import admit_case

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign-token":"foreign-team"}')
    statement = research_service.add_statement(span.id, "shared statement", kind="disclosed_fact")
    cases, links = [], []
    for tenant in ("test-team", "test-team", "foreign-team"):
        case = research_service.add_case(title=tenant, industry_topic="test", created_by="u")
        admit_case(session, case.id, tenant_id=tenant, document_version_id=document.id)
        thesis = research_service.add_thesis(case.id, statement=tenant, created_by="u")
        links.append(research_service.link_evidence(thesis.id, statement.id, role="supports", reason="r", scope={"s": "d"}))
        cases.append(case)
    path = f"/api/v1/documents/{document.id}"
    for token, expected in (("test-tenant-token", links[:2]), ("foreign-token", links[2:])):
        response = api_client.get(path, params={"research_mode": True}, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert {c["link_id"] for c in response.json()["spans"][0]["citations"]} == {str(link.id) for link in expected}
    response = api_client.get(path, params={"research_mode": True, "case_id": str(cases[0].id)})
    assert [c["link_id"] for c in response.json()["spans"][0]["citations"]] == [str(links[0].id)]
    assert api_client.get(path, params={"case_id": str(cases[2].id)}).status_code == 404
    response = api_client.get("/api/v1/documents", params={"limit": 1})
    assert [item["id"] for item in response.json()["items"]] == [str(document.id)]
    assert response.json()["page"]["has_more"] is False


def test_library_pagination_filters_foreign_rows_before_limit_and_rejects_foreign_cursor(
    api_client, session, research_service, document_service, monkeypatch
):
    from tests.tenant_admission import admit_case

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign-token":"foreign-team"}')
    documents = []
    for i, tenant in enumerate(("test-team", "test-team", "foreign-team", "foreign-team", None)):
        version = document_service.freeze(raw=f"page {i}".encode(), source_url=f"https://example.test/{i}")
        case = research_service.add_case(title=f"case {i}", industry_topic="test", created_by="u")
        document_service.attach_to_case(research_case_id=case.id, document_version_id=version.id)
        if tenant is not None:
            admit_case(session, case.id, tenant_id=tenant, document_version_id=version.id)
        else:
            assert api_client.get(f"/api/v1/documents/{version.id}").status_code == 404
            assert api_client.get("/api/v1/documents", params={"case_id": str(case.id)}).status_code == 404
        documents.append(version)
    first = api_client.get("/api/v1/documents", params={"limit": 1}).json()
    assert [item["id"] for item in first["items"]] == [str(documents[1].id)]
    assert first["page"]["has_more"] is True
    second = api_client.get("/api/v1/documents", params={"limit": 1, "cursor": first["page"]["next_cursor"]}).json()
    assert [item["id"] for item in second["items"]] == [str(documents[0].id)]
    assert second["page"] == {"next_cursor": None, "has_more": False}
    foreign = api_client.get("/api/v1/documents", params={"limit": 1}, headers={"Authorization": "Bearer foreign-token"}).json()
    assert api_client.get("/api/v1/documents", params={"cursor": foreign["page"]["next_cursor"]}).status_code == 404
