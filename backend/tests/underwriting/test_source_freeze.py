from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from app.models.ledger import ValidationError
from app.underwriting.services.source_freeze import freeze_manifest, freeze_observations
from app.underwriting.services.source_policy import (
    AuthorizationState,
    DisplayPolicy,
    evaluate_source_policy,
)


CUTOFF = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def manifest_fixture() -> dict[str, object]:
    return {
        "schema_version": "underwriting.source-manifest.v1",
        "sources": [
            {
                "source_id": "catl-annual-report",
                "title": "CATL 2024 annual report",
                "locator": "https://www.cninfo.com.cn/catl-2024.pdf",
                "published_at": "2025-03-15T00:00:00+08:00",
                "first_available_at": "2025-03-15T00:00:00+08:00",
                "retrieved_at": "2025-03-16T00:00:00+08:00",
                "content_sha256": "a" * 64,
                "authority": "issuer_filing",
                "authorization": "authorized",
                "display_policy": "derived_only",
                "provider_capability": "public_http",
                "retention": "hash_locator_and_derived_observations",
            },
            {
                "source_id": "iea-outlook",
                "title": "Global EV Outlook",
                "locator": "https://www.iea.org/reports/global-ev-outlook-2025",
                "published_at": "2025-05-14T00:00:00+08:00",
                "first_available_at": "2025-05-14T00:00:00+08:00",
                "retrieved_at": "2025-05-14T01:00:00+08:00",
                "content_sha256": "b" * 64,
                "authority": "official_industry",
                "authorization": "reference_only",
                "display_policy": "public_excerpt",
                "provider_capability": "public_http",
                "retention": "hash_locator_and_derived_observations",
            },
        ],
    }


def observation_fixture(*, source_id: str = "catl-annual-report") -> dict[str, object]:
    return {
        "definition_key": "company.revenue",
        "definition_version": 1,
        "value": "362012554000",
        "unit": "CNY",
        "observed_start": "2024-01-01T00:00:00+08:00",
        "observed_end": "2024-12-31T23:59:59+08:00",
        "effective_at": "2024-12-31T23:59:59+08:00",
        "available_at": "2025-03-15T00:00:00+08:00",
        "source_id": source_id,
        "source_locator": "p18",
        "dimensions": {"entity": "CATL"},
        "source_role": "reported",
        "research_object_kind": "company",
    }


def observation_fixture_with_conflict() -> list[dict[str, object]]:
    conflicting = observation_fixture()
    conflicting["conflict_group"] = "catl-revenue-2024"
    conflicting["resolution"] = "pending"
    return [conflicting]


def test_freeze_rejects_future_forbidden_or_unreproducible_sources() -> None:
    manifest = manifest_fixture()
    manifest["sources"][0]["first_available_at"] = "2025-05-16T00:00:00+08:00"  # type: ignore[index]
    with pytest.raises(ValidationError, match="source is unavailable at cutoff"):
        freeze_manifest(manifest, cutoff=CUTOFF)

    forbidden = manifest_fixture()
    forbidden["sources"][0]["authorization"] = "forbidden"  # type: ignore[index]
    with pytest.raises(ValidationError, match="source authorization is forbidden"):
        freeze_manifest(forbidden, cutoff=CUTOFF)


def test_freeze_hash_is_stable_under_source_ordering() -> None:
    forward = freeze_manifest(manifest_fixture(), cutoff=CUTOFF)
    reverse_manifest = manifest_fixture()
    reverse_manifest["sources"].reverse()  # type: ignore[union-attr]
    reverse = freeze_manifest(reverse_manifest, cutoff=CUTOFF)
    assert forward.manifest_hash == reverse.manifest_hash
    assert forward.source_ids == ("catl-annual-report", "iea-outlook")
    assert forward.cutoff == CUTOFF


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider_capability", "licensed_feed", "provider capability is unavailable"),
        ("content_sha256", "A" * 64, "content_sha256 must be a sha256 hex digest"),
        ("locator", "", "locator must not be empty"),
        ("published_at", None, "published_at is required"),
        ("first_available_at", None, "first_available_at is required"),
        ("retention", "raw_full_text", "required retention mode is not supported"),
    ],
)
def test_source_policy_rejects_unusable_source_contract(
    field: str, value: object, message: str
) -> None:
    source = deepcopy(manifest_fixture()["sources"][0])  # type: ignore[index]
    source[field] = value
    with pytest.raises(ValidationError, match=message):
        evaluate_source_policy(source)


def test_source_policy_records_authorization_display_and_reproducibility() -> None:
    decision = evaluate_source_policy(manifest_fixture()["sources"][0])  # type: ignore[index]
    assert decision.authorization is AuthorizationState.AUTHORIZED
    assert decision.display_policy is DisplayPolicy.DERIVED_ONLY
    assert decision.reproducible is True
    assert decision.reasons == ()


def test_unresolved_source_conflict_fails_closed() -> None:
    manifest = freeze_manifest(manifest_fixture(), cutoff=CUTOFF)
    with pytest.raises(ValidationError, match="unresolved source conflict"):
        freeze_observations(observation_fixture_with_conflict(), source_manifest=manifest)


def test_observation_freeze_rejects_unknown_and_duplicate_identities() -> None:
    manifest = freeze_manifest(manifest_fixture(), cutoff=CUTOFF)
    with pytest.raises(ValidationError, match="observation references unknown source"):
        freeze_observations([observation_fixture(source_id="unknown")], source_manifest=manifest)
    observation = observation_fixture()
    with pytest.raises(ValidationError, match="observation identity must be unique"):
        freeze_observations([observation, deepcopy(observation)], source_manifest=manifest)


def test_reference_only_source_cannot_be_the_only_reported_company_source() -> None:
    manifest = freeze_manifest(manifest_fixture(), cutoff=CUTOFF)
    with pytest.raises(
        ValidationError,
        match="reference_only source cannot be the sole source of a reported company observation",
    ):
        freeze_observations(
            [observation_fixture(source_id="iea-outlook")], source_manifest=manifest
        )


def test_observation_freeze_normalizes_and_stably_orders_observations() -> None:
    manifest = freeze_manifest(manifest_fixture(), cutoff=CUTOFF)
    later = observation_fixture()
    later["definition_key"] = "company.operating_cash_flow"
    later["source_locator"] = "p20"
    frozen = freeze_observations([later, observation_fixture()], source_manifest=manifest)
    assert [item.definition_key for item in frozen] == [
        "company.operating_cash_flow",
        "company.revenue",
    ]
    assert frozen[1].available_at.tzinfo is UTC
