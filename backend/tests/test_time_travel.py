from datetime import UTC, datetime


def test_snapshot_excludes_source_not_available_at_cutoff(
    assessment_service, future_link, thesis
):
    snapshot = assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 7, 1, tzinfo=UTC)
    )
    assert str(future_link.id) not in snapshot.evidence_link_ids


def test_snapshot_includes_source_available_at_or_before_cutoff(
    assessment_service, thesis, statement, research_service
):
    link = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="available now",
        scope={"segment": "DC"},
    )
    snapshot = assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=UTC)
    )
    assert str(link.id) in snapshot.evidence_link_ids
