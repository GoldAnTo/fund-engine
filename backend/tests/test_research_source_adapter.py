"""Contract tests for governed source adapters."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedEnvelope,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
    SourceUnavailable,
)


def descriptor(**overrides: object) -> SourceDescriptor:
    values: dict[str, object] = {
        "adapter_key": "gildata",
        "provider_identity": "Gildata",
        "allowed_schemes": frozenset({"gildata"}),
        "allowed_hosts": frozenset({"research-report", "announcement"}),
    }
    values.update(overrides)
    return SourceDescriptor(**values)  # type: ignore[arg-type]


def reference(**overrides: object) -> SourceReferenceValue:
    values: dict[str, object] = {
        "adapter_key": "gildata",
        "external_record_id": "report:600000:2026-08-01:abc",
        "external_version": "published:2026-08-01",
        "canonical_url": (
            "gildata://research-report/report:600000:2026-08-01:abc"
        ),
        "title": "示例公司收入跟踪",
        "published_at": datetime(2026, 8, 1, tzinfo=UTC),
        "source_role": "licensed_provider",
        "fetch_locator": {"record_id": "report:600000:2026-08-01:abc"},
        "metadata": {"security_code": "600000", "publisher": "示例机构"},
    }
    values.update(overrides)
    return SourceReferenceValue(**values)  # type: ignore[arg-type]


def test_source_adapter_exposes_exact_narrow_contract():
    public_names = {
        name
        for name, value in vars(SourceAdapter).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }

    assert public_names == {"descriptor", "search", "fetch", "close"}
    assert getattr(SourceAdapter.descriptor.fget, "__isabstractmethod__", False)
    assert all(
        getattr(getattr(SourceAdapter, method), "__isabstractmethod__", False)
        for method in ("search", "fetch", "close")
    )


def test_frozen_reference_matches_contract_value():
    value = reference()

    assert value == SourceReferenceValue(
        adapter_key="gildata",
        external_record_id="report:600000:2026-08-01:abc",
        external_version="published:2026-08-01",
        canonical_url="gildata://research-report/report:600000:2026-08-01:abc",
        title="示例公司收入跟踪",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_role="licensed_provider",
        fetch_locator={"record_id": "report:600000:2026-08-01:abc"},
        metadata={"security_code": "600000", "publisher": "示例机构"},
    )
    with pytest.raises(FrozenInstanceError):
        value.title = "changed"
    with pytest.raises(TypeError):
        value.metadata["publisher"] = "changed"


@pytest.mark.parametrize("provider_identity", ["", "  "])
def test_descriptor_rejects_missing_provider_identity(provider_identity: str):
    with pytest.raises(ValueError, match="provider_identity"):
        descriptor(provider_identity=provider_identity)


@pytest.mark.parametrize("host", ["bad host", "*.gildata", "gildata/path"])
def test_descriptor_rejects_malformed_exact_hosts(host: str):
    with pytest.raises(ValueError, match="allowed_hosts"):
        descriptor(allowed_hosts=frozenset({host}))


def test_reference_rejects_naive_publication_date():
    with pytest.raises(ValueError, match="published_at"):
        reference(published_at=datetime(2026, 8, 1))


@pytest.mark.parametrize(
    "metadata",
    [
        {"Api_Token": "secret"},
        {"nested": {"PASSWORD": "secret"}},
        {"items": [{"authorization": "Bearer secret"}]},
    ],
)
def test_reference_rejects_raw_credentials_recursively(metadata: dict):
    with pytest.raises(ValueError, match="forbidden metadata key"):
        reference(metadata=metadata)


@pytest.mark.parametrize(
    "credential_value",
    [
        "Authorization: Basic Zm9vOmJhcg==",
        "Cookie: session=top-secret",
        "Set-Cookie: session=top-secret; HttpOnly",
        "ＴＯＫＥＮ：top-secret",
    ],
)
def test_reference_rejects_all_secret_header_forms(credential_value: str):
    with pytest.raises(ValueError, match="credential"):
        reference(
            metadata={
                "publisher": "示例机构",
                "transport_note": credential_value,
            }
        )


def test_secret_filter_nfkc_normalizes_keys_and_never_echoes_them():
    secret_key = "ＴＯＫＥＮ"

    with pytest.raises(ValueError) as caught:
        reference(metadata={"publisher": "示例机构", secret_key: "top-secret"})

    rendered = str(caught.value)
    assert secret_key not in rendered
    assert "TOKEN" not in rendered
    assert "top-secret" not in rendered


def test_reference_rejects_disallowed_role():
    with pytest.raises(ValueError, match="source_role"):
        reference(source_role="arbitrary_web")


@pytest.mark.parametrize(
    "canonical_url",
    [
        "https://research-report/report:600000:2026-08-01:abc",
        "gildata://other/report:600000:2026-08-01:abc",
        "gildata://research-report.evil.test/report:600000:2026-08-01:abc",
        "gildata://user@research-report/report:600000:2026-08-01:abc",
        "gildata://research-report/report:600000:2026-08-01:abc?token=secret",
    ],
)
def test_reference_rejects_urls_outside_descriptor_boundary(canonical_url: str):
    value = reference(canonical_url=canonical_url)

    with pytest.raises(ValueError, match="canonical_url"):
        descriptor().validate_reference(value)


def test_reference_rejects_descriptor_adapter_mismatch():
    value = reference()

    with pytest.raises(ValueError, match="adapter_key"):
        descriptor(adapter_key="other").validate_reference(value)


@pytest.mark.parametrize(
    "canonical_url",
    [
        "gildata://research-report:/id",
        "\ngildata://research-report/id",
        "gildata://research-report\n/id",
        "gildata://research-report//id",
        "gildata://research-report/id%2Fchild",
        "gildata://research-report/id%5Cchild",
    ],
)
def test_descriptor_rejects_raw_url_parser_differentials(canonical_url: str):
    with pytest.raises(ValueError, match="canonical_url"):
        descriptor().validate_reference(reference(canonical_url=canonical_url))


def test_descriptor_url_parser_failure_has_no_untrusted_exception_context():
    with pytest.raises(ValueError) as caught:
        descriptor().validate_reference(
            reference(canonical_url="gildata://research-report:bad/id")
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_retrieved_envelope_is_frozen_complete_and_secret_safe():
    value = RetrievedEnvelope(
        content=b"provider text",
        mime_type="text/plain; charset=utf-8",
        final_url="gildata://research-report/stable-id",
        etag="etag-1",
        last_modified="Fri, 01 Aug 2026 00:00:00 GMT",
        provider_request_id="request-1",
        metadata={"source_type": "research_report"},
    )

    assert {item.name for item in fields(value)} == {
        "content",
        "mime_type",
        "final_url",
        "etag",
        "last_modified",
        "provider_request_id",
        "metadata",
    }
    with pytest.raises(FrozenInstanceError):
        value.etag = "changed"
    with pytest.raises(TypeError):
        value.metadata["new"] = "value"


def test_retrieved_envelope_rejects_empty_bytes():
    with pytest.raises(ValueError, match="content"):
        RetrievedEnvelope(
            content=b"",
            mime_type="text/plain",
            final_url="gildata://research-report/id",
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata={},
        )


def test_retrieved_envelope_rejects_nested_forbidden_metadata_key():
    with pytest.raises(ValueError, match="forbidden metadata key"):
        RetrievedEnvelope(
            content=b"text",
            mime_type="text/plain",
            final_url="gildata://research-report/id",
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata={"request": {"Cookie": "session=secret"}},
        )


@pytest.mark.parametrize(
    "final_url",
    [
        "https://user:password@example.test/path",
        "https://example.test/path?%74%6f%6b%65%6e=top-secret",
        "https://example.test/path?%EF%BC%B4%EF%BC%AF%EF%BC%AB%EF%BC%A5%EF%BC%AE=top-secret",
    ],
)
def test_retrieved_envelope_rejects_structural_or_encoded_credentials(
    final_url: str,
):
    with pytest.raises(ValueError) as caught:
        RetrievedEnvelope(
            content=b"text",
            mime_type="text/plain",
            final_url=final_url,
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata={},
        )

    rendered = str(caught.value)
    assert "top-secret" not in rendered
    assert "%74%6f%6b%65%6e" not in rendered
    assert "ＴＯＫＥＮ" not in rendered


@pytest.mark.parametrize(
    "final_url",
    [
        "\nhttps://example.test/path",
        "https://example.test/pa\x00th",
        "https://[malformed/path",
        "https://example.test/path?bad=%ZZ",
    ],
)
def test_retrieved_envelope_rejects_malformed_or_control_urls(final_url: str):
    with pytest.raises(ValueError, match="final_url") as caught:
        RetrievedEnvelope(
            content=b"text",
            mime_type="text/plain",
            final_url=final_url,
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata={},
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_retrieved_envelope_keeps_generic_url_boundary():
    value = RetrievedEnvelope(
        content=b"text",
        mime_type="text/plain",
        final_url="custom+archive://provider.example/path/to/item?view=public",
        etag=None,
        last_modified=None,
        provider_request_id=None,
        metadata={},
    )

    assert value.final_url == (
        "custom+archive://provider.example/path/to/item?view=public"
    )


@pytest.mark.parametrize(
    "final_url",
    [
        "https://example.test/item#",
        "https://example.test/item#public-section",
        "https://example.test/item#%74%6f%6b%65%6e=top-secret",
    ],
)
def test_retrieved_envelope_rejects_all_url_fragments_without_echo(
    final_url: str,
):
    with pytest.raises(ValueError, match="final_url") as caught:
        RetrievedEnvelope(
            content=b"text",
            mime_type="text/plain",
            final_url=final_url,
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata={},
        )

    assert "public-section" not in str(caught.value)
    assert "top-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("etag", "Authorization: Basic top-secret"),
        ("etag", "Basic dXNlcjpwYXNz"),
        ("last_modified", "Cookie: session=top-secret"),
        ("provider_request_id", "ＴＯＫＥＮ=top-secret"),
    ],
)
def test_retrieved_envelope_rejects_secret_bearing_scalar_fields_without_echo(
    field_name: str, unsafe_value: str
):
    values = {
        "etag": None,
        "last_modified": None,
        "provider_request_id": None,
    }
    values[field_name] = unsafe_value

    with pytest.raises(ValueError) as caught:
        RetrievedEnvelope(
            content=b"text",
            mime_type="text/plain",
            final_url="https://example.test/item",
            metadata={},
            **values,
        )

    assert unsafe_value not in str(caught.value)
    assert "top-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "metadata",
    [
        {"headers": {"X-Signature": "opaque-value-a"}},
        {"request_headers": {"Authorization": "Basic top-secret"}},
        {"response_headers": {"Cookie": "session=top-secret"}},
        {"ＲＥＱＵＥＳＴ＿ＨＥＡＤＥＲＳ": {"X-Trace": "safe"}},
        {"http_headers": {"X-Signature": "opaque-value-b"}},
        {"forwarded_headers": [{"X-Signature": "opaque-value-c"}]},
        {"response_header": {"X-Signature": "opaque-value-d"}},
        {"upstream_header_values": {"X-Signature": "opaque-value-e"}},
    ],
)
def test_retrieved_envelope_rejects_header_metadata_containers_without_echo(
    metadata: dict,
):
    with pytest.raises(ValueError) as caught:
        RetrievedEnvelope(
            content=b"text",
            mime_type="text/plain",
            final_url="https://example.test/item",
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata=metadata,
        )

    rendered = str(caught.value)
    assert "request_headers" not in rendered.casefold()
    assert "response_headers" not in rendered.casefold()
    assert "top-secret" not in rendered
    assert "opaque-value" not in rendered


def test_retrieved_envelope_allows_benign_scalar_metadata_containing_header():
    envelope = RetrievedEnvelope(
        content=b"text",
        mime_type="text/plain",
        final_url="https://example.test/item",
        etag=None,
        last_modified=None,
        provider_request_id=None,
        metadata={
            "header_note": "normalized by provider",
            "response_header_count": 3,
        },
    )

    assert envelope.metadata == {
        "header_note": "normalized by provider",
        "response_header_count": 3,
    }


def test_rejected_search_item_preserves_missing_field_without_timestamp():
    item = RejectedSearchItem(
        adapter_key="gildata",
        reason="missing_publication_date",
        external_record_id="report:600000:unknown:abc",
        title="无发布日期研报",
        published_at=None,
        metadata={"security_code": "600000", "provider_identity": "Gildata"},
    )

    assert item.published_at is None
    assert item.reason == "missing_publication_date"
    with pytest.raises(FrozenInstanceError):
        item.reason = "changed"


def test_source_unavailable_has_retryability_and_sanitized_diagnostics():
    error = SourceUnavailable(
        "provider request failed",
        retryable=True,
        diagnostics={"operation": "search", "status": 503},
    )

    assert str(error) == "provider request failed"
    assert error.retryable is True
    assert dict(error.diagnostics) == {"operation": "search", "status": 503}
    with pytest.raises(ValueError, match="forbidden metadata key"):
        SourceUnavailable(
            "failed",
            retryable=True,
            diagnostics={"TOKEN": "secret"},
        )
