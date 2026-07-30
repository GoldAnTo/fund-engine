"""Release-gate acceptance tests (plan Task 9, Step 1).

These tests verify that the ``ReleaseGate`` catches broken traceability and
undated disclosures, and that it passes on a clean seeded database.  Destructive
mutations use raw DBAPI connections to bypass the SQLAlchemy append-only event
guard, simulating data corruption that the ORM would normally reject.
"""
from __future__ import annotations


def test_release_gate_passes_on_clean_seeded_db(release_gate):
    result = release_gate.run()

    assert result.passed is True
    assert result.failures == []

    # The projection check must be skipped (no Neo4j in the test environment),
    # and a skipped check must never cause the gate to fail.
    proj_check = next(
        c for c in result.checks if c["name"] == "projection_rebuilds"
    )
    assert proj_check.get("skipped") is True
    assert proj_check["passed"] is True

    # Every non-skipped check must have passed.
    for check in result.checks:
        if not check.get("skipped"):
            assert check["passed"] is True, (
                f"check {check['name']} unexpectedly failed: {check['failures']}"
            )


def test_release_gate_rejects_missing_source_span(release_gate, seeded_database):
    seeded_database.delete_one_source_span()

    result = release_gate.run()

    assert result.passed is False
    assert "assessment_source_spans_complete" in result.failures

    check = next(
        c for c in result.checks
        if c["name"] == "assessment_source_spans_complete"
    )
    assert check["passed"] is False
    assert check["failures"], "expected failure details for broken traceability"


def test_release_gate_detects_undated_disclosure(release_gate, seeded_database):
    seeded_database.insert_undated_disclosure()

    result = release_gate.run()

    assert result.passed is False
    assert "holding_disclosures_dated" in result.failures

    check = next(
        c for c in result.checks if c["name"] == "holding_disclosures_dated"
    )
    assert check["passed"] is False
    assert check["failures"], "expected failure details for undated disclosure"
