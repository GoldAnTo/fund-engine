"""Rebuild the Neo4j graph projection from the append-only ledger.

Usage::

    python -m app.scripts.rebuild_graph_projection

Reads NEO4J_URL / NEO4J_USER / NEO4J_PASSWORD and DATABASE_URL from the
environment.  The rebuild is idempotent: it first drops only this
application's labelled nodes/edges, then MERGES every ledger entity back into
Neo4j.  It never deletes unrelated Neo4j data.
"""
from __future__ import annotations

from app.services.projection import APP_LABEL, ProjectionService


def main() -> None:
    service = ProjectionService.from_env()
    service.rebuild_all()
    total = service.node_count(APP_LABEL)
    evidence_links = service.node_count("EvidenceLink")
    print(
        f"graph projection rebuilt: {total} application nodes "
        f"({evidence_links} EvidenceLink nodes)"
    )


if __name__ == "__main__":
    main()
