"""Persistence-neutral DTO seam for frozen company-research market inputs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import SourceLineageReference


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValidationError(f"{field_name} must be canonical non-empty text")
    return value


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise ValidationError(f"{field_name} must be a UUID")
    return value


class FrozenMarketSnapshotRole(StrEnum):
    PRICE = "price"
    FX = "fx"
    CAPITAL_STRUCTURE = "capital_structure"
    SECURITY_RIGHTS = "security_rights"


@dataclass(frozen=True, slots=True)
class FrozenRawComponentReference:
    raw_file: str
    raw_hash: str
    source_url: str
    source_locator: str

    def __post_init__(self) -> None:
        _text(self.raw_file, "frozen raw component file")
        _hash(self.raw_hash, "frozen raw component hash")
        _text(self.source_url, "frozen raw component URL")
        _text(self.source_locator, "frozen raw component locator")


@dataclass(frozen=True, slots=True)
class FrozenMarketSnapshotBinding:
    snapshot_id: UUID
    role: FrozenMarketSnapshotRole
    security_external_key: str | None
    source_ref: SourceLineageReference
    snapshot_content_hash: str
    capture_envelope_id: UUID
    capture_content_hash: str
    provenance_role: str
    provider_policy_version: str = "legacy_snapshot_without_exact_provenance.v1"
    raw_components: tuple[FrozenRawComponentReference, ...] = ()

    def __post_init__(self) -> None:
        _uuid(self.snapshot_id, "market snapshot binding snapshot_id")
        if type(self.role) is not FrozenMarketSnapshotRole:
            raise ValidationError("market snapshot binding role must be typed")
        if self.role in {
            FrozenMarketSnapshotRole.PRICE,
            FrozenMarketSnapshotRole.SECURITY_RIGHTS,
        }:
            _text(self.security_external_key, "market snapshot binding security key")
        elif self.security_external_key is not None:
            raise ValidationError("non-security market binding cannot name a security")
        if type(self.source_ref) is not SourceLineageReference:
            raise ValidationError("market snapshot binding source ref must be typed")
        _hash(self.snapshot_content_hash, "market snapshot binding content hash")
        _uuid(self.capture_envelope_id, "market snapshot capture envelope id")
        _hash(self.capture_content_hash, "market snapshot capture content hash")
        if self.provenance_role != "primary":
            raise ValidationError(
                "market snapshot binding provenance role must be primary"
            )
        _text(self.provider_policy_version, "market snapshot provider policy version")
        if not isinstance(self.raw_components, tuple) or not all(
            type(item) is FrozenRawComponentReference for item in self.raw_components
        ):
            raise ValidationError("market snapshot raw components must be immutable")
