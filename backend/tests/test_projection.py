"""Graph-projection tests (neo4j_only).

These tests require a live Neo4j instance reachable via NEO4J_URL.  They are
skipped automatically when NEO4J_URL is unset (see conftest.py).
"""
import pytest


@pytest.mark.neo4j_only
def test_graph_projection_can_be_rebuilt_from_ledger_only(projector, ledger_fixture):
    projector.clear_projection()
    projector.rebuild_all()
    assert projector.node_count("EvidenceLink") == ledger_fixture.evidence_link_count


@pytest.mark.neo4j_only
def test_graph_projection_clear_only_removes_app_labels(projector, ledger_fixture):
    projector.clear_projection()
    projector.rebuild_all()
    assert projector.node_count("EvidenceLink") == ledger_fixture.evidence_link_count
    # Clearing must remove every application-labelled node without error.
    projector.clear_projection()
    assert projector.node_count("EvidenceLink") == 0
