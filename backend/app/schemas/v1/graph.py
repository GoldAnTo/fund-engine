"""Connected relationship graph v1 wire DTOs."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class GraphNodeDTO(V1Model):
    id: str
    kind: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeDTO(V1Model):
    id: str
    semantic_kind: str
    source: str
    target: str
    review_state: str | None = None
    available_at: str | None = None
    valid_interval: dict[str, str | None] | None = None
    source_refs: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPathDTO(V1Model):
    node_ids: list[str]
    edge_ids: list[str]
    label: str


class GraphResponse(V1Model):
    schema_version: Literal["graph/v1"] = "graph/v1"
    basis: HistoricalBasisDTO
    nodes: list[GraphNodeDTO]
    edges: list[GraphEdgeDTO]
    paths: list[GraphPathDTO]
    page: CursorPage
