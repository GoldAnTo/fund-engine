"""Grouped ledger search v1 wire DTOs."""

from typing import Literal

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class SearchHitDTO(V1Model):
    object_type: Literal["case", "thesis", "evidence", "company", "stock", "fund"]
    object_id: str
    title: str
    snippet: str
    case_id: str | None
    review_state: str | None
    available_at: str | None
    deep_link: str


class SearchGroupDTO(V1Model):
    object_type: str
    hits: list[SearchHitDTO]


class SearchResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    groups: list[SearchGroupDTO]
    page: CursorPage
