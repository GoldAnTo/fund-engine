"""Historical read basis: one cutoff interpretation for every v1 read endpoint."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from app.schemas.v1.common import HistoricalBasisDTO


@dataclass(frozen=True)
class HistoricalBasis:
    cutoff: datetime
    is_historical: bool
    ledger_high_watermark: str | None = None
    projection_built_at: datetime | None = None
    projection_schema_version: str | None = None

    @classmethod
    def from_cutoff(
        cls,
        cutoff: datetime | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> "HistoricalBasis":
        if cutoff is None:
            return cls(cutoff=now(), is_historical=False)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        return cls(cutoff=cutoff, is_historical=True)

    def to_dto(self) -> HistoricalBasisDTO:
        return HistoricalBasisDTO(**self.__dict__)
